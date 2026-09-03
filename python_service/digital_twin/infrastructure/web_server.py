import base64
import csv
import errno
import gzip
import hashlib
import html
import json
import mimetypes
import os
import re
import select
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

from ..application.account_service import AccountApplicationService
from ..application.account_watchlist_service import AccountWatchlistService
from ..application.console_read_model_service import ConsoleReadModelService
from ..application.capital_flow_service import CapitalFlowService
from ..application.notification_ai_gate_message import (
    compact_invalidation_line,
    compact_next_action_line,
    decision_transition_presentation,
    execution_headline,
    execution_telegram_message,
    prepend_execution_start_badge,
)
from ..application.notification_replay_service import NotificationReplayService
from ..application.investment_case_query_service import InvestmentCaseQueryService
from ..application.investment_flow_query_service import InvestmentFlowQueryService
from ..application.ontology_catalog_query_service import OntologyCatalogQueryService
from ..application.ontology_diagnostics_service import OntologyDiagnosticsService
from ..application.research_evidence_governance_service import ResearchEvidenceGovernanceService
from ..domain.accounts import split_symbols
from ..domain.instrument_timeline import InstrumentTimelineQuery
from ..application.symbol_universe_service import DEFAULT_SYMBOL_SEEDS, SUPPORTED_MARKETS, seed_symbol
from ..domain.events import (
    APP_ITEM_REMOVED,
    APP_ITEM_UPDATED,
    APP_MEMORY_RECORDED,
    APP_MEMORY_REMOVED,
    APP_MEMORY_UPDATED,
    APP_PROFILE_UPDATED,
    CHAT_MESSAGE_APPENDED,
    MONITORING_ALERTS_DETECTED,
    MONITORING_CYCLE_COMPLETED,
    MONITORING_SNAPSHOT_COLLECTED,
    NOTIFICATION_JOB_QUEUED,
    NOTIFICATION_RULE_UPDATED,
    NOTIFICATION_TEMPLATE_UPDATED,
    NOTIFICATION_TEST_REQUESTED,
    SETTINGS_UPDATED,
    SYMBOL_UNIVERSE_REFRESH_FAILED,
    SYMBOL_UNIVERSE_REFRESH_REQUESTED,
    SYMBOL_UNIVERSE_REFRESHED,
    DomainEvent,
    research_evidence_lifecycle_events,
)
from ..domain.message_types import (
    DEFAULT_ALERT_RULES,
    DEFAULT_CADENCE,
    INVESTMENT_INSIGHT,
    public_message_catalog,
    user_managed_notification_types,
    visible_notification_template_types,
)
from ..domain.notification_icon_policy import notification_message_icon, notification_title_with_context_icon
from ..domain.notification_ai_gate_text import user_friendly_ai_text
from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_delivery_explanation import build_customer_delivery_explanation
from ..domain.ontology_decision_state import ACTION_ENVELOPE_STATUS_LABELS
from ..domain.data_freshness import age_minutes
from ..domain.market_hours import DEFAULT_MARKET_HOUR_SESSIONS
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_rules import CONDITION_TYPE_LABELS, NotificationRuleConfig
from ..domain.notification_reverse_reasoning import build_notification_reverse_reasoning_trace
from ..domain.notifications import NotificationJob
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES, NotificationTemplate, alert_context, template_variables
from ..domain.ontology_inference_ledger import inference_trace_ledger_payload
from ..domain.ontology_worlds import PORTFOLIO_WORLD_TYPE, portfolio_world_id, world_type_from_id
from ..domain.investment_ubiquitous_language import (
    LANGUAGE_REGISTRY_SETTING_KEY,
    audit_user_facing_investment_text,
    investment_language_registry,
    normalize_investment_language_registry,
    propose_investment_language_changes,
    validate_investment_language_registry,
)
from ..domain.investment_research import NewsCollectionTarget
from ..domain.investment_analysis import investment_decision_key
from ..domain.investment_evidence_governance import claim_quality_summary
from ..domain.investment_model import INVESTMENT_MODEL_VERSION, investment_model_projection
from ..domain.investment_reasoning.rule_inventory import reasoning_rule_inventory
from ..domain.prompt_evidence_admission import assess_prompt_evidence
from ..domain.news_ai_analysis import has_mojibake, local_news_ai_analysis, apply_news_ai_analysis, news_ai_analysis_is_current
from ..domain.parsing import parse_assignments
from ..domain.portfolio import utc_now_iso
from ..domain.symbol_universe import symbol_search_symbol_candidates
from ..news_intelligence.application.analyze_article import evidence_eligibility
from ..infrastructure.event_bus import EventBus, JsonEventLog, default_event_bus
from ..infrastructure.api_performance import ApiPerformanceRegistry
from ..infrastructure.external_signal_utils import ExternalCircuitOpen, ExternalRateLimited, external_call_target, guarded_external_call
from ..infrastructure.mock_market import mock_market_payload, mock_market_scenario_list
from ..infrastructure.model_reviewer import codex_cli_arguments
from ..infrastructure.ontology_graph_store import ontology_repository_from_settings
from ..infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from ..infrastructure.runtime_identity import runtime_identity
from ..infrastructure.stale_read_model import StaleReadModelCache
from ..infrastructure import operational_store as stores
from ..infrastructure.operational_error_reporting import operational_error_reporter, report_runtime_error
from ..infrastructure.service_factory import (
    build_investment_calendar_candidate_service,
    build_investment_calendar_discovery_service,
    build_investment_calendar_research_service,
    build_investment_calendar_runner,
    build_investment_calendar_service,
    build_instrument_timeline_query_service,
    build_investment_strategy_proposal_service,
    build_investment_brain_service,
    build_historical_replay_job_service,
    build_hypothesis_development_service,
    build_trade_execution_service,
    build_notification_queue_runner,
    build_official_calendar_sync_service,
    build_ontology_lab_service,
    build_ontology_reasoning_queue_probe,
    build_ontology_reasoning_runner,
    build_rule_change_candidate_service,
    build_symbol_universe_service,
    build_external_data_collection_runner,
    build_news_analysis_enrichment_runner,
    build_market_data_collection_runner,
    build_monitor_runner,
    build_flow_lens_service,
    flow_lens_snapshot,
    investment_analysis_snapshot,
)
from ..infrastructure.share_access import (
    SHARE_ROLE_LOCAL_OWNER,
    SHARE_ROLE_OWNER,
    SHARE_ROLE_VIEWER,
    ShareAccess,
    anonymous_access,
    authenticate_share_token,
    direct_loopback_request,
    issue_share_session,
    local_owner_access,
    owner_tokens,
    share_access_from_cookie,
    share_mode_enabled,
    share_session_cookie,
    viewer_tokens,
)
from ..infrastructure.share_runtime import (
    active_share_runtime_state,
    fixed_access_url,
    fixed_entry_url,
    request_share_tunnel_rotation,
)
from ..infrastructure.flow_lens_read_model import FlowLensReadModel
from ..infrastructure.settings import ROOT_DIR, read_json, runtime_settings, save_runtime_settings, write_private_json
from ..infrastructure.toss_snapshots import build_snapshot


PUBLIC_DIR = ROOT_DIR / "public"
LOCAL_APP_STORE_PATH = ROOT_DIR / "data" / "store.json"
WEB_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
MEMORY_CATEGORIES = ["identity", "preference", "finance", "travel", "asset", "schedule", "work", "other"]
DOMAIN_TYPES = ["stock", "trip", "asset", "schedule", "task", "note"]
MAX_BODY_BYTES = 1024 * 1024
WEB_PROXY_API_GUARD_STATE: Dict[str, object] = {}
FLOW_LENS_READ_MODEL = None
FLOW_LENS_READ_MODEL_LOCK = threading.Lock()
ONTOLOGY_INFERENCE_LEDGER_READ_MODEL = StaleReadModelCache(
    "ontology-inference-ledger",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
ONTOLOGY_DIAGNOSTICS_READ_MODEL = StaleReadModelCache(
    "ontology-diagnostics",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
INVESTMENT_MODEL_READ_MODEL = StaleReadModelCache(
    "investment-model",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
DASHBOARD_READ_MODEL = StaleReadModelCache(
    "console-dashboard",
    ttl_seconds=20,
    retry_cooldown_seconds=10,
)
PORTFOLIO_CONSOLE_READ_MODEL = StaleReadModelCache(
    "console-portfolio",
    ttl_seconds=10,
    retry_cooldown_seconds=5,
)
MARKET_INSTRUMENTS_READ_MODEL = StaleReadModelCache(
    "console-market-instruments",
    ttl_seconds=5,
    retry_cooldown_seconds=3,
)
MARKET_EVIDENCE_READ_MODEL = StaleReadModelCache(
    "console-market-evidence",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)
OPERATIONS_HEALTH_READ_MODEL = StaleReadModelCache(
    "operations-health",
    ttl_seconds=30,
    retry_cooldown_seconds=15,
)
EXTERNAL_DATA_STATUS_READ_MODEL = StaleReadModelCache(
    "external-data-status",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
RESEARCH_EVIDENCE_SUMMARY_READ_MODEL = StaleReadModelCache(
    "research-evidence-summary",
    ttl_seconds=90,
    retry_cooldown_seconds=20,
)
RESEARCH_EVIDENCE_PAGE_READ_MODEL = StaleReadModelCache(
    "research-evidence-page",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)
ONTOLOGY_RULEBOX_READ_MODEL = StaleReadModelCache(
    "ontology-rulebox",
    ttl_seconds=120,
    retry_cooldown_seconds=30,
)
ONTOLOGY_CATALOG_SUMMARY_READ_MODEL = StaleReadModelCache(
    "ontology-catalog-summary",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
ONTOLOGY_CATALOG_PAGE_READ_MODEL = StaleReadModelCache(
    "ontology-catalog-page",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
ONTOLOGY_EXPERIMENT_STATUS_READ_MODEL = StaleReadModelCache(
    "ontology-experiment-status",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
ONTOLOGY_EXPERIMENT_LIST_READ_MODEL = StaleReadModelCache(
    "ontology-experiment-list",
    ttl_seconds=30,
    retry_cooldown_seconds=15,
)
HYPOTHESIS_TEMPLATE_READ_MODEL = StaleReadModelCache(
    "hypothesis-template-list",
    ttl_seconds=120,
    retry_cooldown_seconds=30,
)
HYPOTHESIS_POLICY_VERSION_READ_MODEL = StaleReadModelCache(
    "hypothesis-policy-version-list",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)
DECISION_LIST_READ_MODEL = StaleReadModelCache(
    "decision-list",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)
INVESTMENT_BRAIN_LIST_READ_MODEL = StaleReadModelCache(
    "investment-brain-list",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)
HYPOTHESIS_WORKSPACE_READ_MODEL = StaleReadModelCache(
    "hypothesis-workspace",
    ttl_seconds=30,
    retry_cooldown_seconds=10,
)
API_PERFORMANCE = ApiPerformanceRegistry()
WATCHLIST_REFRESH_LOCK = threading.Lock()
WATCHLIST_REFRESH_STATE: Dict[str, object] = {
    "running": False,
    "pending": False,
    "accountIds": set(),
    "symbols": set(),
    "lastStatus": "idle",
    "lastError": "",
    "lastFinishedAt": "",
}
SYMBOL_UNIVERSE_REFRESH_LOCK = threading.Lock()
SYMBOL_UNIVERSE_REFRESH_STATE: Dict[str, object] = {
    "jobId": "",
    "running": False,
    "status": "idle",
    "markets": set(),
    "pendingMarkets": set(),
    "completedMarkets": set(),
    "results": [],
    "summary": {},
    "requestedAt": "",
    "startedAt": "",
    "finishedAt": "",
    "lastError": "",
    "stage": "idle",
    "currentMarket": "",
    "stageItemCount": 0,
    "updatedAt": "",
}

NON_CADENCE_MESSAGE_GUIDES = {
    "modelReview": "판단 변화 알림이 발생하면 별도 워커가 충분히 분석한 뒤 보냅니다.",
    "workHandoff": "작업이 끝나고 커밋, 검증, 푸시, 재시작 결과를 공유할 때 보냅니다.",
    "notification": "사용자가 직접 만든 일반 알림이나 시스템 안내가 있을 때 보냅니다.",
    "default": "타입별 템플릿이 없을 때 fallback으로 사용됩니다.",
}


def now() -> str:
    return utc_now_iso()


def operational_read_settings() -> Dict[str, object]:
    """Return runtime settings for a read-only web request.

    History retention is a scheduled maintenance concern.  Running it while a
    screen is opening turns a simple read into an unbounded write path and can
    leave the first Flow Lens response waiting on a busy MySQL connection.
    """
    settings = dict(runtime_settings(fast_operational_read=True))
    settings["_skipOperationalHistoryRetention"] = "1"
    settings["_skipOperationalSchemaBootstrap"] = "1"
    settings["_skipNotificationRuleDefaultsSeed"] = "1"
    return settings


def flow_lens_data_freshness(generated_at: object, settings: Dict[str, object] = None) -> Dict[str, object]:
    """Expose one freshness contract for every Flow Lens consumer.

    ``toss.mode`` describes the provider connection, not the age of the
    monitor projection.  Keeping this calculation in the API prevents each
    screen from treating an old live connection as current market data.
    """
    settings = settings or {}
    try:
        max_age = int(float(settings.get("marketDataMaxAgeMinutes") or settings.get("dataFreshnessDefaultMaxAgeMinutes") or 30))
    except (TypeError, ValueError):
        max_age = 30
    max_age = max(1, min(1440, max_age))
    age = age_minutes(generated_at)
    if age is None:
        status, label, reason = "unknown", "기준시각 없음", "스냅샷 생성 시각이 없습니다."
    elif age > max_age:
        status, label, reason = "stale", "데이터 지연", "최근 스냅샷이 신선도 기준을 넘었습니다."
    else:
        status, label, reason = "fresh", "데이터 신선", "최근 스냅샷이 신선도 기준 안에 있습니다."
    return {
        "status": status,
        "label": label,
        "reason": reason,
        "ageMinutes": age,
        "maxAgeMinutes": max_age,
        "generatedAt": str(generated_at or ""),
    }


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def websocket_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def websocket_frame(payload, opcode: int = 0x1) -> bytes:
    raw = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    length = len(raw)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(length)
    elif length <= 65535:
        header.extend([126, (length >> 8) & 0xFF, length & 0xFF])
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    return bytes(header) + raw


def socket_read_exact(sock, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return b""
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_websocket_frame(sock):
    header = socket_read_exact(sock, 2)
    if len(header) < 2:
        return 0x8, b""
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    masked = bool(header[1] & 0x80)
    if length == 126:
        length = int.from_bytes(socket_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(socket_read_exact(sock, 8), "big")
    mask = socket_read_exact(sock, 4) if masked else b""
    payload = socket_read_exact(sock, length) if length else b""
    if masked and mask:
        payload = bytes(payload[index] ^ mask[index % 4] for index in range(len(payload)))
    return opcode, payload


class RealtimeHub:
    def __init__(self):
        self.clients = set()
        self.recent_events: List[DomainEvent] = []
        self.lock = threading.Lock()

    def add(self, client) -> None:
        with self.lock:
            self.clients.add(client)

    def remove(self, client) -> None:
        with self.lock:
            self.clients.discard(client)

    def status(self) -> Dict[str, object]:
        with self.lock:
            connected = len(self.clients)
        return {"connectedClients": connected}

    def remember_event(self, event: DomainEvent) -> None:
        with self.lock:
            self.recent_events.insert(0, event)
            self.recent_events = self.recent_events[:50]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.lock:
            return list(self.recent_events[:limit])

    def send(self, client, payload, opcode: int = 0x1) -> bool:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            client.sendall(websocket_frame(body, opcode=opcode))
            return True
        except OSError:
            self.remove(client)
            return False

    def broadcast(self, event_type: str, payload: Dict[str, object] = None) -> None:
        message = {
            "type": event_type,
            "payload": dict(payload or {}),
            "occurredAt": now(),
        }
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            self.send(client, message)

    def broadcast_event(self, event: DomainEvent) -> None:
        self.remember_event(event)
        self.broadcast(event.name, {"event": event.to_dict(), **dict(event.payload or {})})


REALTIME_HUB = RealtimeHub()


class RealtimeEventBridge:
    def __init__(self):
        try:
            self.inner = default_event_bus()
        except Exception:  # noqa: BLE001 - domain events should fall back when optional MySQL is offline.
            self.inner = EventBus()
            self.inner.subscribe_all(JsonEventLog().handle)

    def publish(self, event: DomainEvent) -> None:
        self.inner.publish(event)
        REALTIME_HUB.broadcast_event(event)

    def dispatch_recorded(self, event: DomainEvent) -> None:
        """Fan out an event already written in the source transaction."""
        self.inner.dispatch_recorded(event)
        REALTIME_HUB.broadcast_event(event)


def publish_domain_event(event: DomainEvent) -> DomainEvent:
    RealtimeEventBridge().publish(event)
    return event


def new_domain_event(name: str, aggregate_id: str, payload: Dict[str, object] = None) -> DomainEvent:
    return publish_domain_event(DomainEvent(name=name, aggregate_id=aggregate_id, payload=dict(payload or {})))


def realtime_event_payload(event: DomainEvent) -> Dict[str, object]:
    return {
        "name": event.name,
        "eventId": event.event_id,
        "aggregateId": event.aggregate_id,
        "occurredAt": event.occurred_at,
        "payload": event.payload,
    }


def realtime_event_summary(event: DomainEvent) -> Dict[str, object]:
    """Expose only the fields needed to refresh a desktop status indicator."""
    payload = dict(event.payload or {})
    summary_keys = [
        "accountId", "accountLabel", "count", "status", "generatedAt", "symbol",
        "symbols", "messageType", "jobId", "sourceEventId", "snapshotCount", "eventCount",
    ]
    return {
        "name": event.name,
        "eventId": event.event_id,
        "aggregateId": event.aggregate_id,
        "occurredAt": event.occurred_at,
        "payload": {key: payload[key] for key in summary_keys if key in payload},
    }


def realtime_status_payload() -> Dict[str, object]:
    store_warning = ""
    settings = operational_read_settings()
    try:
        event_log = stores.event_log(settings)
        counts = event_log.event_counts()
        latest_by_name = event_log.latest_events_by_name([
            MONITORING_CYCLE_COMPLETED,
            MONITORING_ALERTS_DETECTED,
            MONITORING_SNAPSHOT_COLLECTED,
        ])
        latest_events = event_log.latest_events(limit=12)
    except Exception as error:  # noqa: BLE001 - status API should degrade when optional MySQL is offline.
        store_warning = str(error)[:240]
        latest_events = REALTIME_HUB.latest_events(limit=12)
        counts = {}
        for event in latest_events:
            counts[event.name] = counts.get(event.name, 0) + 1
        latest_by_name = {}
    monitoring = {}
    if latest_by_name.get(MONITORING_CYCLE_COMPLETED):
        monitoring["cycle"] = realtime_event_summary(latest_by_name[MONITORING_CYCLE_COMPLETED])
    if latest_by_name.get(MONITORING_ALERTS_DETECTED):
        monitoring["alerts"] = realtime_event_summary(latest_by_name[MONITORING_ALERTS_DETECTED])
    if latest_by_name.get(MONITORING_SNAPSHOT_COLLECTED):
        monitoring["snapshot"] = realtime_event_summary(latest_by_name[MONITORING_SNAPSHOT_COLLECTED])
    try:
        notification_jobs = notification_queue_store(settings).summary()
    except Exception as error:  # noqa: BLE001 - notification queue may share the same optional MySQL backend.
        store_warning = store_warning or str(error)[:240]
        notification_jobs = {
            "pending": 0,
            "awaiting_ai": 0,
            "processing": 0,
            "done": 0,
            "superseded": 0,
            "suppressed": 0,
            "failed": 0,
        }
    try:
        ai_inference_queue = stores.ai_inference_queue_store(settings).summary()
    except Exception as error:  # noqa: BLE001 - expose the notification queue even if AI storage is unavailable.
        store_warning = store_warning or str(error)[:240]
        ai_inference_queue = {"pendingCount": 0, "retryCount": 0, "processingCount": 0, "failedCount": 0}
    return {
        **REALTIME_HUB.status(),
        "events": counts,
        "latestEvents": [realtime_event_summary(event) for event in latest_events],
        "monitoring": monitoring,
        "notificationJobs": notification_jobs,
        "aiInferenceQueue": ai_inference_queue,
        "storeWarning": store_warning,
    }


def _external_data_status_source_payload() -> Dict[str, object]:
    try:
        payload = dict(build_external_data_collection_runner().status() or {})
        try:
            payload["newsAnalysis"] = build_news_analysis_enrichment_runner().status()
        except Exception as error:  # noqa: BLE001 - official collection status remains independently useful.
            payload["newsAnalysis"] = {"status": "unavailable", "error": str(error)[:240]}
        return payload
    except Exception as error:  # noqa: BLE001 - status remains inspectable while MySQL starts.
        return {
            "enabled": False,
            "status": "unavailable",
            "error": str(error)[:500],
        }


def external_data_status_payload(force: bool = False) -> Dict[str, object]:
    def load() -> Dict[str, object]:
        payload = _external_data_status_source_payload()
        if str(payload.get("status") or "").lower() in {"error", "unavailable"}:
            raise RuntimeError(str(payload.get("error") or "외부 데이터 상태를 읽지 못했습니다."))
        return payload

    return cached_api_payload(
        EXTERNAL_DATA_STATUS_READ_MODEL,
        "all-providers",
        load,
        force=force,
    )


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


def configured(value) -> str:
    return str(value or "").strip()


def request_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def cached_api_payload(
    cache: StaleReadModelCache,
    key: str,
    loader,
    force: bool = False,
    unavailable_status: str = "unavailable",
    blocking_first_load: bool = True,
) -> Dict[str, object]:
    """Attach freshness metadata to a persistent stale-while-revalidate read model."""

    snapshot = cache.get_or_refresh(
        str(key or "default"),
        loader,
        force=force,
        blocking_first_load=blocking_first_load,
    )
    payload = dict(snapshot.get("payload") or {})
    if not payload:
        payload = {
            "status": "warming" if snapshot.get("refreshing") else unavailable_status,
            "error": str(snapshot.get("lastError") or "읽기 모델을 준비하고 있습니다."),
        }
    payload["readCache"] = {
        "stale": bool(snapshot.get("stale")),
        "ageSeconds": int(snapshot.get("ageSeconds") or 0),
        "refreshing": bool(snapshot.get("refreshing")),
        "lastSuccessAt": str(snapshot.get("lastSuccessAt") or ""),
        "lastError": str(snapshot.get("lastError") or ""),
    }
    return payload


def default_store() -> Dict[str, object]:
    stamped = now()
    return {
        "version": 1,
        "profile": {
            "ownerName": "Namsoon",
            "assistantName": "Twin",
            "preferredLanguage": "한국어",
            "answerStyle": "핵심부터 말하고, 필요한 근거와 실행 단계를 짧게 정리한다.",
            "tone": "담백하고 실무적인 말투. 과장하지 않는다.",
            "decisionStyle": "선택지를 비교하고 리스크와 다음 행동을 분리해서 판단한다.",
            "riskStyle": "투자와 자산 판단은 보수적으로 접근하고, 확신이 낮으면 추가 확인을 요구한다.",
            "financePolicy": "주식은 매수/매도 지시가 아니라 관찰 포인트, 리스크, 체크리스트 중심으로 돕는다.",
            "travelPolicy": "여행은 예산, 이동 동선, 피로도, 예약 마감일을 함께 본다.",
            "schedulePolicy": "일정은 오늘 처리할 것, 미룰 것, 위임할 것을 나눠서 관리한다.",
            "assetPolicy": "자산은 계좌번호나 인증 정보 없이 요약 단위로 기록하고, 목표와 현금흐름 중심으로 관리한다.",
            "boundaries": "법률, 세무, 투자 판단은 최종 결정을 대신하지 않는다. 민감한 정보는 저장하지 않는다.",
        },
        "memories": [
            {
                "id": "mem-default-1",
                "content": "사용자는 한국어로 명확하고 실용적인 답변을 선호한다.",
                "category": "preference",
                "status": "approved",
                "importance": 4,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "mem-default-2",
                "content": "비서는 주식, 여행 계획, 자산관리, 스케줄 관리를 우선 도메인으로 다룬다.",
                "category": "identity",
                "status": "approved",
                "importance": 5,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "items": [
            {
                "id": "item-default-1",
                "type": "task",
                "title": "비서에게 나의 투자 기준 입력",
                "status": "open",
                "date": "",
                "notes": "예: 장기 투자, 단기 매매 회피, 현금 비중 선호, 관심 섹터",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "item-default-2",
                "type": "schedule",
                "title": "이번 주 일정 정리",
                "status": "planned",
                "date": "",
                "notes": "중요한 회의, 마감일, 개인 약속을 입력한다.",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "messages": [
            {
                "id": "msg-default-1",
                "role": "assistant",
                "content": "무엇부터 정리할까요? 주식 관심 목록, 여행 계획, 자산 현황, 이번 주 일정 중 하나를 말해주면 바로 기록하고 다음 행동으로 나누겠습니다.",
                "createdAt": stamped,
            }
        ],
    }


def app_store(settings: Dict[str, object] = None):
    # Opening the web shell is a read path. Schema bootstrap and retention are
    # owned by the service manager, so the first browser request must not pay
    # their startup cost.
    return stores.app_store(settings or operational_read_settings())


def read_store() -> Dict[str, object]:
    fallback = default_store()
    try:
        parsed = app_store().load()
    except Exception as error:  # noqa: BLE001 - bootstrap must remain available when optional MySQL is offline.
        parsed = read_json(LOCAL_APP_STORE_PATH, {})
        if isinstance(parsed, dict):
            parsed.setdefault("metadata", {})
            parsed["metadata"]["operationalStoreWarning"] = str(error)[:240]
    if not parsed:
        parsed = fallback
        try:
            app_store().replace(parsed)
        except Exception:  # noqa: BLE001 - local fallback keeps the web console readable.
            write_private_json(LOCAL_APP_STORE_PATH, parsed)
    return {
        **fallback,
        **parsed,
        "profile": {**fallback["profile"], **dict(parsed.get("profile") or {})},
        "memories": parsed.get("memories") if isinstance(parsed.get("memories"), list) else [],
        "items": parsed.get("items") if isinstance(parsed.get("items"), list) else [],
        "messages": parsed.get("messages") if isinstance(parsed.get("messages"), list) else [],
    }


def save_store(mutator):
    store = read_store()
    mutator(store)
    try:
        app_store().replace(store)
    except Exception:  # noqa: BLE001 - local fallback keeps manual notes usable without MySQL.
        write_private_json(LOCAL_APP_STORE_PATH, store)
    return store


def snapshot_payload() -> Dict[str, object]:
    store = read_store()
    return {
        "profile": store["profile"],
        "memories": store["memories"],
        "items": store["items"],
        "messages": store["messages"],
    }


def portfolio_lifecycle_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    portfolio_id = first_query(query, "portfolioId") or "portfolio:" + account_id
    return stores.investment_domain_store(operational_read_settings()).latest_portfolio_lifecycle(portfolio_id)


def review_action_plan_payload(plan_id: str, decision: str, body: Dict[str, object]) -> Dict[str, object]:
    try:
        return build_trade_execution_service().review_plan(
            plan_id,
            decision,
            str(body.get("reviewer") or "local-user"),
            str(body.get("reason") or ""),
        )
    except ValueError as error:
        return {"status": "error", "error": str(error)}


def execute_action_plan_payload(plan_id: str) -> Dict[str, object]:
    try:
        return build_trade_execution_service().submit_plan(plan_id)
    except ValueError as error:
        return {"status": "error", "error": str(error)}


def record_action_plan_fills_payload(plan_id: str, body: Dict[str, object]) -> Dict[str, object]:
    try:
        return build_trade_execution_service().record_fills(
            plan_id,
            body.get("fills") if isinstance(body.get("fills"), list) else [],
            str(body.get("completedAt") or ""),
        )
    except (TypeError, ValueError) as error:
        return {"status": "error", "error": str(error)}


def share_runtime_status_payload(access: ShareAccess = None, settings: Dict[str, object] = None) -> Dict[str, object]:
    settings = settings if isinstance(settings, dict) else {}
    resolved_access = access or (anonymous_access() if share_mode_enabled() else local_owner_access())
    runtime = active_share_runtime_state()
    entry_url = str(runtime.get("fixedEntryUrl") or fixed_entry_url(settings)).strip()
    viewer_token = next(iter(viewer_tokens()), "")
    owner_token = next(iter(owner_tokens()), "")
    viewer_access_url = str(runtime.get("fixedViewerUrl") or fixed_access_url(entry_url, "share_token", viewer_token)).strip()
    owner_access_url = str(runtime.get("fixedOwnerUrl") or fixed_access_url(entry_url, "owner_token", owner_token)).strip()
    current_viewer_url = str(runtime.get("viewerUrl") or "").strip()
    current_owner_url = str(runtime.get("ownerUrl") or "").strip()
    privileged = resolved_access.role in {SHARE_ROLE_LOCAL_OWNER, SHARE_ROLE_OWNER}
    selected_access_url = owner_access_url if resolved_access.role == SHARE_ROLE_OWNER else viewer_access_url
    selected_current_url = current_owner_url if resolved_access.role == SHARE_ROLE_OWNER else current_viewer_url
    identity = dict(runtime_identity())
    identity["startedAt"] = WEB_PROCESS_STARTED_AT
    return {
        "enabled": share_mode_enabled(),
        "active": bool(runtime),
        "provider": str(runtime.get("provider") or "cloudflared"),
        "baseUrl": str(runtime.get("baseUrl") or "") if runtime else "",
        "fixedEntryUrl": entry_url,
        "fixedAccessUrl": selected_access_url if privileged else "",
        "currentAccessUrl": selected_current_url if privileged else "",
        "updatedAt": str(runtime.get("updatedAt") or ""),
        "targetPublishStatus": str(runtime.get("targetPublishStatus") or ("waiting" if runtime else "inactive")),
        "targetPublishedAt": str(runtime.get("targetPublishedAt") or ""),
        "targetPublishError": str(runtime.get("targetPublishError") or "")[:500] if privileged else "",
        "rotationStatus": str(runtime.get("rotationStatus") or ("active" if runtime else "inactive")),
        "rotationCount": int(runtime.get("rotationCount") or 0),
        "rotationMinutes": int(runtime.get("rotationMinutes") or 0),
        "rotationGraceSeconds": int(runtime.get("rotationGraceSeconds") or 0),
        "tunnelStartedAt": str(runtime.get("tunnelStartedAt") or ""),
        "renewAt": str(runtime.get("renewAt") or ""),
        "lastRotationAt": str(runtime.get("lastRotationAt") or ""),
        "lastRotationReason": str(runtime.get("lastRotationReason") or ""),
        "lastRotationStatus": str(runtime.get("lastRotationStatus") or ""),
        "lastRotationError": str(runtime.get("lastRotationError") or "")[:500] if privileged else "",
        "lastHealthCheckAt": str(runtime.get("lastHealthCheckAt") or ""),
        "lastHealthStatus": str(runtime.get("lastHealthStatus") or "unknown"),
        "lastHealthError": str(runtime.get("lastHealthError") or "")[:500] if privileged else "",
        "consecutiveHealthFailures": int(runtime.get("consecutiveHealthFailures") or 0),
        "runtimeIdentity": identity,
        "accessLinkPolicy": "fragment-only",
    }


def settings_status_payload(access: ShareAccess = None) -> Dict[str, object]:
    settings = runtime_settings()
    from ..domain.notification_ai_prompt_release import active_notification_ai_prompt_release

    prompt_release = active_notification_ai_prompt_release(settings).to_public_dict()
    public_keys = [
        "appTheme",
        "appTimezone",
        "watchlistSymbols",
        "mysqlUrl",
        "mysqlHost",
        "mysqlPort",
        "mysqlDatabase",
        "mysqlUser",
        "mysqlUnixSocket",
        "mysqlTablePartitioning",
        "tossApiBaseUrl",
        "kisEnv",
        "kisBaseUrl",
        "kisWebSocketUrl",
        "kisRealtimeWebSocketEnabled",
        "kisRealtimeWebSocketSymbols",
        "kisRealtimeWebSocketIncludeConfiguredInReasoning",
        "kisRealtimeWebSocketMaxSymbols",
        "kisRealtimeWebSocketCollectSeconds",
        "kisRealtimeWebSocketEventIntervalSeconds",
        "kisRealtimeWebSocketReconnectSeconds",
        "kisRealtimeWebSocketTimeoutSeconds",
        "kisMarketSignalsEnabled",
        "kisMarketSignalMaxSymbols",
        "kisMarketSignalCacheMinutes",
        "kisMarketSignalGapSeconds",
        "kisMarketSignalPreferLiveDuringMarketHours",
        "kisMarketSignalLiveRefreshSeconds",
        "kisMarketSignalUnchangedStaleCount",
        "notifyProvider",
        "notifyLinkUrl",
        "fxRates",
        "fairValueFormula",
        "ontologyRelationRules",
        "aiPromptTemplates",
        "aiPromptPolicy",
        "notificationAiGateEnabled",
        "notificationAiGateMessageTypes",
        "notificationAiUseCodex",
        "notificationAiModel",
        "notificationAiReasoningEffort",
        "notificationAiTimeoutSeconds",
        "notificationAiDeliveryDeadlineSeconds",
        "notificationAiAttemptWatchdogSeconds",
        "notificationAiTypeDbFallbackEnabled",
        "notificationAiFallbackOnFirstFailure",
        "notificationAiComparisonRepairReasoningEffort",
        "notificationAiComparisonRepairTimeoutSeconds",
        "notificationAiQueueWorkerCount",
        "localAiMaxConcurrentProcesses",
        "localAiInvestmentReservedProcesses",
        "localAiCapacityWaitSeconds",
        "notificationAiCapacityWaitSeconds",
        "notificationAiQueueBatchSize",
        "notificationAiQueueIntervalSeconds",
        "notificationAiQueueLeaseSeconds",
        "notificationAiQueueHeartbeatSeconds",
        "notificationAiQueueMaxAttempts",
        "notificationAiQueueRetrySeconds",
        "notificationAiQueueMaxPromptBytes",
        "notificationAiQueueRetentionHours",
        "investmentBrainMinimumHypothesisCount",
        "investmentBrainMaximumHypothesisCount",
        "investmentBrainInferenceBoxLimit",
        "investmentBrainResearchEnabled",
        "investmentBrainResearchMaxRounds",
        "investmentBrainResearchEvidenceLimit",
        "investmentBrainResearchMinimumVerifiedCount",
        "investmentBrainResearchMinimumSourceTrustState",
        "researchClaimRequireVerifiedForInvestment",
        "researchClaimOfficialVerificationEnabled",
        "researchClaimMinimumIndependentSources",
        "researchClaimCrossSourceWindowHours",
        "researchClaimSimilarityThreshold",
        "researchClaimSourceRegistry",
        "investmentBrainResearchCooldownMinutes",
        "investmentBrainOutcomeObservationMinutes",
        "investmentBrainOutcomeEpisodeBatchSize",
        "investmentBrainOutcomeMaxDelayMinutes",
        "investmentActionPlanExpiryMinutes",
        "investmentActionPlanSlicePct",
        "investmentActionPlanSliceCount",
        "investmentExecutionQuoteDriftPct",
        "investmentExecutionSnapshotMaxAgeMinutes",
        "investmentBrainNotificationResearchEnabled",
        "investmentBrainNovelHypothesisAiEnabled",
        "investmentBrainNovelHypothesisAiTimeoutSeconds",
        "modelName",
        "modelHypothesis",
        "modelTimingScenario",
        "modelTimingSymbols",
        "operatorReasoningReportEnabled",
        "alertRules",
        "alertThresholds",
        "relationRuleThresholds",
        "alertCadenceMinutes",
        "ontologyTypeDbEnabled",
        "ontologyProjectionGraphCacheEnabled",
        "ontologyProjectionGraphCacheTtlSeconds",
        "ontologyProjectionGraphCacheMaxEntries",
        "ontologyProjectionGraphPersistentCacheEnabled",
        "ontologyProjectionGraphPersistentCacheTtlSeconds",
        "ontologyProjectionGraphPersistentCacheMaxEntries",
        "ontologyProjectionGraphPersistentCacheMaxPayloadBytes",
        "ontologyDecisionEpisodeContextPerSymbolLimit",
        "ontologyDecisionEpisodeContextMaxEpisodes",
        "ontologyDecisionEpisodeContextHypothesisLimit",
        "ontologyDecisionEpisodeContextOutcomeLimit",
        "ontologyMonitorInlineProjectionEnabled",
        "ontologyReasoningEnabled",
        "ontologyReasoningIntervalSeconds",
        "ontologyReasoningBatchSize",
        "ontologyReasoningMaxSymbolsPerRun",
        "ontologyReasoningAdaptiveBatchEnabled",
        "ontologyReasoningAdaptiveBatchSteadySymbols",
        "ontologyReasoningAdaptiveBatchBurstSymbols",
        "ontologyReasoningAdaptiveBatchPendingThreshold",
        "ontologyReasoningAdaptiveBatchAgeSeconds",
        "ontologyReasoningAdaptiveBatchRuntimeGuardSeconds",
        "ontologyReasoningAdaptiveBatchBudgetSeconds",
        "ontologyReasoningAdaptiveBatchBacklogBurstEnabled",
        "ontologyReasoningAdaptiveBatchBacklogBurstAgeSeconds",
        "ontologyReasoningMailboxEnabled",
        "ontologyReasoningMailboxIngressEnabled",
        "ontologyReasoningMailboxBatchSize",
        "ontologyReasoningMailboxRetentionHours",
        "ontologyReasoningWorkLeaseSeconds",
        "ontologyReasoningWorkRetrySeconds",
        "ontologyReasoningQueueProbeCacheSeconds",
        "ontologyReasoningSourceFreshnessEnabled",
        "ontologyReasoningRealtimeEventMaxAgeMinutes",
        "ontologyReasoningResearchEventMaxAgeMinutes",
        "ontologyReasoningTelemetryHistoryLimit",
        "reasoningEngineSharedPremiseInlineRetryCount",
        "reasoningEngineSharedPremiseInlineRetryMaxSeconds",
        "typedbNativeRuleTargetSymbolLimit",
        "typedbNativeRuleTargetParallelism",
        "typedbNativeRuleSubjectFanoutEnabled",
        "typedbNativeRuleSubjectParallelism",
        "typedbNativeRuleTotalReadParallelism",
        "typedbNativeRuleTargetWorkShardingEnabled",
        "typedbNativeRuleAdaptiveTargetShardingEnabled",
        "typedbNativeRuleAdaptiveTargetShardingLookbackRuns",
        "typedbNativeRuleAdaptiveTargetShardingParallelism",
        "typedbNativeRuleSelectionEnabled",
        "typedbIncrementalEquivalenceAuditSamplePct",
        "typedbNativeRuleParallelism",
        "typedbNativeRuleExecutionBudgetSeconds",
        "ontologyReasoningMinIntervalSeconds",
        "ontologyReasoningUrgentMinIntervalSeconds",
        "ontologyReasoningCriticalMinIntervalSeconds",
        "ontologyReasoningMarketMinIntervalSeconds",
        "ontologyReasoningResearchMinIntervalSeconds",
        "ontologyReasoningObservationFollowupTargetLimit",
        "ontologyReasoningVerifiedSnapshotFastDrainEnabled",
        "ontologyReasoningBacklogDrainNoCooldownEnabled",
        "ontologyReasoningBacklogDrainNoCooldownAgeSeconds",
        "ontologyReasoningProjectionRetrySeconds",
        "ontologyProjectionCircuitProbeRetrySeconds",
        "ontologyReasoningBackpressureEnabled",
        "ontologyReasoningBackpressureFactor",
        "ontologyReasoningBackpressureMaxSeconds",
        "ontologyReasoningFairnessMaxWaitSeconds",
        "ontologyReasoningFairnessDrainEnabled",
        "ontologyReasoningFairnessDrainMinIntervalSeconds",
        "ontologyReasoningProcessIsolationEnabled",
        "ontologyReasoningPersistentWorkerEnabled",
        "ontologyReasoningExecutionTimeoutSeconds",
        "ontologyReasoningExecutionTimeoutGraceSeconds",
        "ontologyReasoningExecutionTimeoutBackoffSeconds",
        "ontologyReasoningPreNativeTimeoutBackoffSeconds",
        "ontologyReasoningQueueAlertEnabled",
        "ontologyReasoningQueueWarningAgeMinutes",
        "ontologyReasoningQueueCriticalAgeMinutes",
        "ontologyReasoningQueueWarningPendingCount",
        "ontologyReasoningQueueCriticalPendingCount",
        "ontologyReasoningQueueWarningOverdueSymbols",
        "ontologyReasoningQueueCriticalOverdueSymbols",
        "ontologyReasoningQueueConsecutiveObservations",
        "ontologyReasoningQueueNoProgressMinutes",
        "ontologyReasoningQueueAlertReminderMinutes",
        "investmentAlertCoverageEnabled",
        "investmentAlertCoverageReconcileSeconds",
        "investmentAlertCoverageLookbackHours",
        "investmentAlertCoverageDeadlineSeconds",
        "investmentAlertCoverageStarvationMinCandidates",
        "investmentAlertCoverageConsecutiveObservations",
        "investmentAlertCoverageReminderMinutes",
        "ontologyProjectionAuditStaleAfterSeconds",
        "ontologyReasoningMaintenanceEnabled",
        "ontologyReasoningMaintenanceIntervalSeconds",
        "ontologyAboxMaintenanceEnabled",
        "ontologyAboxMaintenanceIntervalSeconds",
        "ontologyAboxMaintenanceWorldTypes",
        "ontologyAboxMaintenanceMaxManifestsPerRun",
        "ontologyAboxMaintenanceMaxDeleteBatchesPerRun",
        "ontologyAboxMaintenanceDeleteBatchSize",
        "ontologyAboxMaintenanceKeepInactiveManifestCount",
        "ontologyAboxMaintenanceWarningInactiveManifestCount",
        "ontologyAboxMaintenanceCriticalInactiveManifestCount",
        "ontologyAboxMaintenanceAdaptiveDrainEnabled",
        "ontologyAboxMaintenanceAdaptiveDrainMaxDeleteBatchesPerRun",
        "ontologyAboxMaintenanceAdaptiveDrainCriticalRunsBeforeIncrease",
        "ontologyAboxMaintenanceProcessIsolationEnabled",
        "ontologyAboxMaintenanceExecutionTimeoutSeconds",
        "ontologyAboxMaintenanceExecutionTimeoutGraceSeconds",
        "ontologyAboxMaintenanceExecutionReserveSeconds",
        "ontologyAboxMaintenanceSliceSeconds",
        "ontologyAboxMaintenanceEstimatedDeleteBatchSeconds",
        "ontologyAboxMaintenanceMaxReasoningDeferralSeconds",
        "ontologyAboxMaintenanceBusyRetrySeconds",
        "ontologyAboxMaintenancePriorityInactiveManifestCount",
        "ontologyAboxMaintenanceYieldEnabled",
        "ontologyAboxMaintenanceYieldAfterSeconds",
        "ontologyAboxMaintenanceYieldWindowSeconds",
        "ontologyAboxMaintenanceYieldRequestTtlSeconds",
        "ontologyAboxMaintenanceYieldCooldownSeconds",
        "ontologyAboxMaintenanceYieldInventoryMaxAgeSeconds",
        "ontologyWorldProjectionDeferWhenReasoningPending",
        "ontologyInferenceDetailOutboxEnabled",
        "ontologyInferenceDetailIntervalSeconds",
        "ontologyInferenceDetailBatchSize",
        "ontologyInferenceDetailLeaseSeconds",
        "ontologyInferenceDetailDeferWhenReasoningPending",
        "ontologyInferenceDetailMaxAttempts",
        "ontologyInferenceDetailExecutionTimeoutSeconds",
        "ontologyInferenceDetailExecutionTimeoutGraceSeconds",
        "ontologyInferenceDetailProcessIsolationEnabled",
        "ontologyInferenceDetailCompletedRetentionHours",
        "ontologyInferenceDetailMaxResultBytes",
        "ontologyAboxMaintenanceDeferWhenReasoningPending",
        "ontologyRuntimeProjectionSloSeconds",
        "ontologyRuntimeInferenceSloSeconds",
        "ontologyRuntimeSloConsecutiveBreachCount",
        "ontologyRuntimeAuditWindowRuns",
        "ontologyScopeIntegrityAuditEnabled",
        "ontologyScopeIntegrityAuditIntervalMinutes",
        "ontologyScopeIntegrityAuditBatchSize",
        "ontologyScopeRepairRetryMinutes",
        "ontologyScopeRepairVerificationMinutes",
        "ontologyAboxMaintenanceAdaptiveDrainBacklogGrowthRunsBeforeIncrease",
        "ontologyAboxMaintenanceAdaptiveDrainMaxConsecutiveWorldRuns",
        "ontologyReasoningUrgentReviewLevels",
        "temporalWindowPeriods",
        "temporalWindowHistoryLimit",
        "ontologyLabAutoApplyEnabled",
        "ontologyLabAutoApplyValidationStates",
        "ontologyLabAutoApplyNeedsReviewEnabled",
        "ontologyLabNotifyEnabled",
        "ontologyRuleCandidateAiEnabled",
        "ontologyRuleCandidateAiUseCodex",
        "ontologyRuleCandidateAiCommand",
        "ontologyRuleCandidateAiTimeoutSeconds",
        "ontologyRuleCandidateAiIntervalMinutes",
        "ontologyRuleCandidateAiMaxCandidates",
        "ontologyTenantId",
        "ontologySharedMarketTenantId",
        "ontologySharedMarketWorldRetentionHours",
        "ontologySharedMarketWorldMaxSymbols",
        "ontologySharedMarketWorldAsyncProjectionEnabled",
        "materialityGateEnabled",
        "marketMaterialityPriceChangePct",
        "marketMaterialityTrendDistancePct",
        "marketMaterialityTrendDistanceChangePct",
        "marketMaterialityVolumeRatio",
        "marketMaterialityInvestorFlowRatioPct",
        "marketSignalTransitionPolicyEnabled",
        "marketSignalPersistenceObservations",
        "marketSignalPricePersistenceObservations",
        "marketSignalPriceResetPct",
        "marketSignalPriceImmediatePct",
        "marketSignalOrderbookEnterPct",
        "marketSignalOrderbookExitPct",
        "marketSignalTradeStrengthBand",
        "marketSignalTradeStrengthExitBand",
        "marketSignalVolumeEnterRatio",
        "marketSignalVolumeExitRatio",
        "marketSignalInvestorFlowExitPct",
        "marketSignalTrendCrossBufferPct",
        "marketSignalTrendCrossExitPct",
        "marketSignalTrendDistanceExitPct",
        "marketSignalDataStatePersistenceObservations",
        "typedbAddress",
        "typedbUser",
        "typedbAllowDefaultPassword",
        "typedbDatabase",
        "typedbTlsEnabled",
        "typedbTimeoutSeconds",
        "typedbRetryCount",
        "typedbPersistentDriverEnabled",
        "typedbProjectionCoordinatorEnabled",
        "typedbProjectionCoordinatorLeaseSeconds",
        "typedbProjectionCoordinatorRetrySeconds",
        "typedbStaticNodeBatchSize",
        "typedbStaticWriteTransactionQueryCount",
        "symbolUniverseMaxAgeHours",
        "typedbInferenceGenerationKeepCount",
        "typedbAutoResetEnabled",
        "typedbAgeResetEnabled",
        "typedbDataRetentionHours",
        "typedbDataMaxSizeMb",
        "typedbMinimumFreeSpaceMb",
        "typedbCapacityGuardCheckIntervalSeconds",
        "typedbCapacityGuardStaleSeconds",
        "typedbCapacitySharedSampleMaxAgeSeconds",
        "typedbCapacityThrottlePercent",
        "typedbCapacityAutoRotateEnabled",
        "typedbCapacityProactiveRotatePercent",
        "typedbCapacityAutoRotatePercent",
        "typedbCapacityAutoRotateFreeSpaceMb",
        "typedbCapacityCriticalPercent",
        "typedbCapacityAutoRotateCooldownMinutes",
        "typedbCapacityAutoRotateFailureRetrySeconds",
        "typedbBlueGreenMinimumHeadroomMb",
        "typedbBlueGreenEstimatedCandidateMaxMb",
        "typedbCapacityMaintenanceMaxManifests",
        "typedbCapacityMaintenanceMaxDeleteBatches",
        "typedbCapacityMaintenanceDeleteBatchSize",
        "typedbStartupWaitSeconds",
        "externalApiFetchIntervalMinutes",
        "externalAlphaEnabled",
        "externalAlphaDailyRequestBudget",
        "externalAlphaQuotaCooldownMinutes",
        "externalAlphaFundamentalsEnabled",
        "externalAlphaFundamentalsMaxSymbols",
        "externalYFinanceEnabled",
        "externalYFinanceMaxSymbols",
        "externalYFinanceHistoryPeriod",
        "externalYFinanceHistoryInterval",
        "externalYFinanceHistoryRows",
        "externalYFinanceFinancialPeriods",
        "externalYFinanceTabularRows",
        "externalYFinanceOptionExpirations",
        "externalYFinanceOptionsMaxRows",
        "externalYFinanceEarningsLimit",
        "externalYFinanceNewsLimit",
        "externalYFinancePriceMaxAgeMinutes",
        "externalYFinanceOptionsMaxAgeMinutes",
        "externalYFinanceNewsMaxAgeMinutes",
        "externalYFinanceAnalystMaxAgeMinutes",
        "externalYFinanceFundamentalMaxAgeMinutes",
        "externalCoinGeckoEnabled",
        "externalCoinGeckoFetchIntervalMinutes",
        "externalFredEnabled",
        "externalFredSeries",
        "externalFredTimeoutSeconds",
        "externalCryptoIds",
        "externalAlphaMaxSymbols",
        "externalSecEnabled",
        "externalSecMaxSymbols",
        "externalSecCompanyCiks",
        "externalSecContactEmail",
        "externalSecUserAgent",
        "externalSecDocumentTextEnabled",
        "externalSecDocumentTextMaxChars",
        "externalDartEnabled",
        "externalDartLookbackDays",
        "externalDartCorpCodes",
        "externalDartCompanyFundamentalsEnabled",
        "externalDartDocumentTextEnabled",
        "externalDartDocumentTextMaxChars",
        "externalDartDocumentMaxPerSymbol",
        "externalNewsEnabled",
        "externalNewsProvider",
        "externalNewsMaxSymbols",
        "externalNewsLookbackHours",
        "externalResearchEvidenceMaxItems",
        "newsCollectionEnabled",
        "newsCollectionIntervalSeconds",
        "newsCollectionRunBudgetSeconds",
        "newsCollectionMaxSymbols",
        "newsCollectionLookbackMinutes",
        "newsCollectionPerSymbolLimit",
        "newsCollectionProviders",
        "newsCollectionInternationalProviders",
        "newsCollectionKoreanProviders",
        "newsCollectionBoundedParallelEnabled",
        "newsCollectionProviderParallelism",
        "newsCollectionPrimaryProviderCount",
        "newsCollectionPrimaryMinimumItems",
        "newsCollectionGoogleKrEnabled",
        "newsCollectionGoogleUsEnabled",
        "newsCollectionYahooSearchEnabled",
        "newsCollectionYahooRssEnabled",
        "newsCollectionGdeltSyncEnabled",
        "newsCollectionGoogleOriginalUrlResolveEnabled",
        "newsCollectionGoogleOriginalUrlMaxPerTarget",
        "newsCollectionGoogleOriginalUrlMaxPerRun",
        "newsCollectionArticleBodyMinimumChars",
        "newsCollectionArticleBodyCacheMinutes",
        "newsCollectionArticleBodyFailureCacheMinutes",
        "newsCollectionArticleBodyCacheMaxEntries",
        "newsCollectionQualityGateEnabled",
        "newsCollectionMinimumRelevanceState",
        "newsCollectionMinimumMaterialityState",
        "newsCollectionMinimumSourceTrustState",
        "newsCollectionRequireArticleBody",
        "newsDigestMinimumRelevanceState",
        "newsDigestMinimumMaterialityState",
        "newsDigestMinimumNeutralMaterialityState",
        "newsDigestMinimumSourceTrustState",
        "newsDigestRequireKoreanTitleTranslation",
        "newsCollectionRequireArticleBodyForRss",
        "newsCollectionIncludeWatchlist",
        "newsCollectionIncludeHoldings",
        "newsCollectionRateLimitSeconds",
        "newsEvidenceCleanupEnabled",
        "newsEvidenceMaxAgeMinutes",
        "newsEvidenceCleanupBatchSize",
        "newsEvidenceCleanupIntervalSeconds",
        "newsEvidenceKeepUndated",
        "researchEvidenceWriteBatchSize",
        "mysqlDeadlockRetryCount",
        "mysqlDeadlockRetryBaseMilliseconds",
        "mysqlDeadlockRetryMaxMilliseconds",
        "newsArticleBodyFailureWarnRate",
        "newsArticleBodyFailureMinimumCount",
        "dataPipelineHealthDeteriorationConsecutiveRuns",
        "dataPipelineHealthRecoveryConsecutiveRuns",
        "dataPipelineHealthFailureConsecutiveRuns",
        "dataPipelineHealthNormalConsecutiveRuns",
        "newsAiAnalysisEnabled",
        "newsAiAnalysisUseCodex",
        "newsAiAnalysisCommand",
        "newsAiAnalysisTimeoutSeconds",
        "newsAiAnalysisInlineTimeoutSeconds",
        "newsAiAnalysisInlineEnabled",
        "newsAiAnalysisAsyncEnabled",
        "newsAiAnalysisWorkerIntervalSeconds",
        "newsAiAnalysisWorkerBatchSize",
        "newsAiAnalysisWorkerScanLimit",
        "newsAiAnalysisRetryMinutes",
        "researchClaimCorpusLimit",
        "investmentCalendarEnabled",
        "investmentCalendarIntervalSeconds",
        "investmentCalendarDefaultWindowDays",
        "investmentCalendarReminderLookbackMinutes",
        "investmentCalendarAutoExtractEnabled",
        "investmentCalendarAutoExtractRegisterUndated",
        "investmentCalendarAutoExtractReviewEnabled",
        "investmentCalendarCandidateDefaultTime",
        "investmentCalendarAiResearchEnabled",
        "investmentCalendarAiResearchRunCollection",
        "investmentCalendarAiResearchEvidenceLimit",
        "investmentCalendarAiResearchCandidateLimit",
        "investmentCalendarDiscoveryEnabled",
        "investmentCalendarDiscoveryIntervalHours",
        "investmentCalendarDiscoveryMaxSymbols",
        "investmentCalendarDiscoveryHorizonDays",
        "investmentCalendarOfficialMacroSyncEnabled",
        "investmentCalendarOfficialMacroSyncIntervalHours",
        "investmentCalendarOfficialMacroSyncRateLimitSeconds",
        "investmentCalendarOfficialMacroSyncTimeoutSeconds",
        "investmentCalendarOfficialEarningsSyncEnabled",
        "investmentCalendarOfficialEarningsLookbackDays",
        "investmentCalendarOfficialEarningsMaxSymbols",
        "investmentCalendarOfficialEarningsRateLimitSeconds",
        "investmentCalendarBokPolicyDecisionEnabled",
        "investmentCalendarBokPolicyDecisionTimeKst",
        "investmentCalendarBokPolicyDecisionLookaheadYears",
        "dartDisclosureAiAnalysisEnabled",
        "dartDisclosureAiUseCodex",
        "dartDisclosureAiCommand",
        "dartDisclosureAiTimeoutSeconds",
        "notificationQueueIntervalSeconds",
        "notificationQueueBatchSize",
        "notificationSendGapSeconds",
        "notificationProcessingStaleMinutes",
        "monitorAccountQueueEnabled",
        "monitorAccountIntervalSeconds",
        "monitorAccountBatchSize",
        "monitorAccountLockSeconds",
        "marketObservationRawDeliveryMode",
        "marketObservationImmediatePriceChangePct",
        "marketObservationImmediateCadenceMinutes",
        "marketDataMaxAgeMinutes",
        "marketSignalDataCollectionEnabled",
        "marketSignalDataBatchSize",
        "dataFreshnessEnabled",
        "newsDigestFreshnessGateEnabled",
        "dataFreshnessNewsDigestMaxAgeMinutes",
        "dataFreshnessDefaultMaxAgeMinutes",
        "dataFreshnessQuoteMaxAgeMinutes",
        "dataFreshnessKisPriceMaxAgeMinutes",
        "dataFreshnessKisMicrostructureMaxAgeMinutes",
        "dataFreshnessKisInvestorMaxAgeMinutes",
        "dataFreshnessExternalMaxAgeMinutes",
        "dataFreshnessExternalEquityMaxAgeMinutes",
        "dataFreshnessExternalCryptoMaxAgeMinutes",
        "dataFreshnessMacroMaxAgeMinutes",
        "dataFreshnessDisclosureMaxAgeMinutes",
        "externalSignalCacheMaxAgeMinutes",
        "externalPublicDataStockEnabled",
        "externalPublicDataReferenceEnabled",
        "externalPublicDataTimeoutSeconds",
        "externalDataPublicStockCadenceSeconds",
        "externalDataPublicStockFreshnessSeconds",
        "externalDataPublicStockMaxPartitions",
        "externalDataPublicReferenceCadenceSeconds",
        "externalDataPublicReferenceFreshnessSeconds",
        "externalDataPublicReferenceMaxPartitions",
        "aiValuationAutoProposalEnabled",
        "aiValuationCurrentPriceAnchorEnabled",
        "valuationReviewOverrides",
        "aiValuationPreferredParValue",
        "aiValuationPreferredRiskSpreadPct",
        "aiValuationPreferredRequiredYieldPct",
        "aiValuationPreferredMinimumMarginPct",
        "aiValuationBaselineMinimumMarginPct",
    ]
    public = {key: settings.get(key, "") for key in public_keys}
    public.update({
        "tossClientId": "",
        "tossClientSecret": "",
        "tossAccountSeq": "",
        "kisAppKey": "",
        "kisAppSecret": "",
        "telegramBotToken": "",
        "telegramChatId": "",
        "operationsTelegramBotToken": "",
        "operationsTelegramChatId": "",
        "alphaVantageApiKey": "",
        "coingeckoApiKey": "",
        "fredApiKey": "",
        "opendartApiKey": "",
        "publicDataPortalServiceKey": "",
        "typedbPassword": "",
        "mysqlPassword": "",
    })
    for optional_key in ["valuationAssumptions", "marketSignalInputs"]:
        if configured(settings.get(optional_key)):
            public[optional_key] = settings[optional_key]
    resolved_access = access or (anonymous_access() if share_mode_enabled() else local_owner_access())
    return {
        "settings": public,
        "notificationAiPromptRelease": prompt_release,
        "configured": {
            "tossClientId": bool(settings.get("tossClientId")),
            "tossClientSecret": bool(settings.get("tossClientSecret")),
            "tossAccountSeq": bool(settings.get("tossAccountSeq")),
            "kisAppKey": bool(settings.get("kisAppKey")),
            "kisAppSecret": bool(settings.get("kisAppSecret")),
            "telegramBotToken": bool(settings.get("telegramBotToken")),
            "telegramChatId": bool(settings.get("telegramChatId")),
            "operationsTelegramBotToken": bool(settings.get("operationsTelegramBotToken")),
            "operationsTelegramChatId": bool(settings.get("operationsTelegramChatId")),
            "alphaVantageApiKey": bool(settings.get("alphaVantageApiKey")),
            "coingeckoApiKey": bool(settings.get("coingeckoApiKey")),
            "fredApiKey": bool(settings.get("fredApiKey")),
            "opendartApiKey": bool(settings.get("opendartApiKey")),
            "publicDataPortalServiceKey": bool(settings.get("publicDataPortalServiceKey")),
            "typedbAddress": bool(settings.get("typedbAddress")),
            "typedbPassword": bool(settings.get("typedbPassword")),
            "mysqlPassword": bool(settings.get("mysqlPassword")),
        },
        "locked": bool(resolved_access.shared and not resolved_access.writable),
        "shareAccess": resolved_access.to_public_dict(),
        "shareRuntime": share_runtime_status_payload(resolved_access, settings),
        "runtimeIdentity": {
            **runtime_identity(),
            "startedAt": WEB_PROCESS_STARTED_AT,
        },
    }


def save_settings_payload(payload: Dict[str, object], access: ShareAccess = None) -> Dict[str, object]:
    requested = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    save_runtime_settings(requested if isinstance(requested, dict) else {})
    status = settings_status_payload(access)
    new_domain_event(
        SETTINGS_UPDATED,
        "runtime",
        {
            "keys": sorted([str(key) for key in (requested or {}).keys()]) if isinstance(requested, dict) else [],
            "configured": status.get("configured") or {},
        },
    )
    return status


def time_series_platform_status_payload() -> Dict[str, object]:
    from .time_series_factory import build_time_series_adapters, initialize_time_series_registry

    settings = operational_read_settings()
    adapters = build_time_series_adapters(settings)
    registry = initialize_time_series_registry(settings, adapters)
    health = {backend_id: adapter.health() for backend_id, adapter in adapters.items()}
    for backend_id, payload in health.items():
        registry.update_health(backend_id, payload)
    return {
        "control": registry.control(),
        "deployments": registry.list(),
        "health": health,
        "queue": stores.time_series_projection_outbox_store(settings).summary(),
    }


def reasoning_engine_platform_status_payload(
    query: Dict[str, List[str]] = None,
) -> Dict[str, object]:
    from .reasoning_engine_factory import build_reasoning_engine_platform

    platform = build_reasoning_engine_platform(operational_read_settings())
    state = platform.initialize()
    include_history = request_bool(first_query(query or {}, "historical"), False)
    return platform.current_status(state, include_history=include_history)


def reasoning_engine_comparisons_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    from .reasoning_engine_factory import build_reasoning_engine_platform

    settings = runtime_settings()
    deployment_id = str(first_query(query, "deploymentId") or "ontology-v2-shadow")
    try:
        limit = max(1, min(200, int(first_query(query, "limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    store = stores.reasoning_engine_comparison_store(settings)
    platform = build_reasoning_engine_platform(settings)
    platform.initialize()
    release = platform.release_identity(deployment_id)
    historical = str(first_query(query, "historical") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    release_fingerprint = "" if historical else str(release.get("releaseFingerprint") or "")
    cohort_id = "" if historical else str(release.get("validationCohortId") or "")
    return {
        "release": release,
        "historical": historical,
        "summary": store.summary(
            deployment_id,
            limit=limit,
            candidate_release_fingerprint=release_fingerprint,
            validation_cohort_id=cohort_id,
        ),
        "comparisons": store.latest(
            deployment_id,
            limit=limit,
            candidate_release_fingerprint=release_fingerprint,
            validation_cohort_id=cohort_id,
        ),
    }


def investment_reasoning_cases_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = runtime_settings()
    store = stores.investment_reasoning_case_store(settings)
    subject_store = stores.subject_decision_case_store(settings)
    subject_case_id = str(first_query(query, "subjectCaseId") or "").strip()
    if subject_case_id:
        subject_case = subject_store.get(subject_case_id)
        batch_case = store.get(subject_case.batch_case_id) if subject_case else None
        return {
            "status": "ok" if subject_case else "not-found",
            "subjectCase": subject_case.to_dict() if subject_case else {},
            "batchCase": batch_case.to_dict() if batch_case else {},
            "auditTrail": subject_store.audit_trail(subject_case_id) if subject_case else [],
        }
    case_id = str(first_query(query, "caseId") or "").strip()
    if case_id:
        reasoning_case = store.get(case_id)
        return {
            "status": "ok" if reasoning_case else "not-found",
            "case": reasoning_case.to_dict() if reasoning_case else {},
            "subjectCases": [
                item.to_dict() for item in subject_store.for_batch(case_id)
            ] if reasoning_case else [],
        }
    deployment_id = str(first_query(query, "deploymentId") or "").strip()
    symbol = str(first_query(query, "symbol") or "").upper().strip()
    release_fingerprint = str(first_query(query, "releaseFingerprint") or "").strip()
    try:
        limit = max(1, min(200, int(first_query(query, "limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    cases = store.latest(deployment_id=deployment_id, symbol=symbol, limit=limit)
    return {
        "status": "ok",
        "summary": store.summary(deployment_id, release_fingerprint),
        "cases": [reasoning_case.to_dict() for reasoning_case in cases],
    }


def _ontology_rulebox_source_payload() -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()


def ontology_rulebox_payload(force: bool = False, blocking_first_load: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        ONTOLOGY_RULEBOX_READ_MODEL,
        "active",
        _ontology_rulebox_source_payload,
        force=force,
        blocking_first_load=blocking_first_load,
    )


def ontology_rulebox_summary_payload() -> Dict[str, object]:
    payload = ontology_rulebox_payload(blocking_first_load=True)
    profile = payload.get("nativeReasoningProfile") if isinstance(payload.get("nativeReasoningProfile"), dict) else {}
    inventory = reasoning_rule_inventory(
        item for item in payload.get("rules") or [] if isinstance(item, dict)
    )
    return {
        key: payload.get(key)
        for key in [
            "configured", "saved", "status", "source", "graphStore", "reason", "engineVersion",
            "ruleCount", "conditionCount", "derivationCount", "relationTypes", "versionCount",
            "ruleboxSnapshotId", "ruleboxRulesHash", "ruleboxShortHash", "readCache",
        ]
        if key in payload
    } | {
        "ruleInventory": inventory,
        "nativeReasoningProfile": {
            key: profile.get(key)
            for key in [
                "version", "status", "ruleCount", "readyRuleCount", "partialRuleCount",
                "blockedRuleCount", "supportedConditionCount", "unsupportedConditionCount",
            ]
            if key in profile
        }
    }


def ontology_catalog_api_payload(section: str, query: Dict[str, List[str]]) -> Dict[str, object]:
    """Read one ontology catalog section without mutating graph state."""

    section_id = str(section or "summary").strip().lower()
    account_id = str(first_query(query, "accountId") or first_query(query, "account") or "").strip()
    world_id = ontology_world_id_from_query(query)

    def service() -> OntologyCatalogQueryService:
        settings = operational_read_settings()
        include_lineage = section_id == "lineage"
        return OntologyCatalogQueryService(
            ontology_repository=ontology_repository_from_settings(settings),
            hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
            decision_episode_store=(
                stores.investment_decision_episode_store(settings)
                if include_lineage or section_id == "summary"
                else None
            ),
            notification_job_store=stores.notification_job_store(settings) if include_lineage else None,
            statistical_signal_store=stores.statistical_model_signal_store(settings) if section_id == "summary" else None,
            rulebox_provider=lambda: ontology_rulebox_payload(blocking_first_load=True),
        )

    if section_id == "summary":
        cache_key = "|".join([world_id or "none", account_id or "none"])
        return cached_api_payload(
            ONTOLOGY_CATALOG_SUMMARY_READ_MODEL,
            cache_key,
            lambda: service().summary(world_id=world_id, account_id=account_id),
            force=request_bool(first_query(query, "refresh"), False),
            blocking_first_load=False,
        )
    if section_id == "rules" and not ONTOLOGY_RULEBOX_READ_MODEL.snapshot("active").get("hasData"):
        warming = ontology_rulebox_payload(blocking_first_load=False)
        return {
            "status": "warming",
            "section": section_id,
            "items": [],
            "count": 0,
            "total": 0,
            "nextCursor": "",
            "readCache": warming.get("readCache") or {},
        }
    if section_id == "lineage":
        return service().lineage(
            item_type=str(first_query(query, "type") or ""),
            item_id=str(first_query(query, "id") or ""),
            world_id=world_id,
            account_id=account_id,
            symbol=str(first_query(query, "symbol") or "").upper(),
        )
    list_args = {
        "section": section_id,
        "query": str(first_query(query, "query") or first_query(query, "q") or ""),
        "cursor": str(first_query(query, "cursor") or ""),
        "limit": safe_int(first_query(query, "limit"), 40, 1, 100),
        "bounded_context": str(first_query(query, "boundedContext") or first_query(query, "context") or ""),
        "enabled": str(first_query(query, "enabled") or "").lower(),
        "scope": str(first_query(query, "scope") or ""),
        "state": str(first_query(query, "state") or ""),
        "symbol": str(first_query(query, "symbol") or "").upper(),
        "account_id": account_id,
        "market_id": str(first_query(query, "marketId") or first_query(query, "market") or ""),
        "world_id": world_id,
        "rule_kind": str(first_query(query, "ruleKind") or ""),
        "theory_family": str(first_query(query, "theoryFamily") or ""),
        "validation_status": str(first_query(query, "validationStatus") or ""),
    }
    cache_key = json.dumps(list_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return cached_api_payload(
        ONTOLOGY_CATALOG_PAGE_READ_MODEL,
        cache_key,
        lambda: service().list_section(**list_args),
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )


def investment_flow_api_payload(query: Dict[str, List[str]], episode_id: str = "") -> Dict[str, object]:
    """Read the persisted decision lineage without running TypeDB on an HTTP request."""

    settings = operational_read_settings()
    service = InvestmentFlowQueryService(
        decision_episode_store=stores.investment_decision_episode_store(settings),
        notification_job_store=stores.notification_job_store(settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
    )
    if episode_id:
        return service.detail(str(episode_id or ""))
    return service.summary(
        account_id=str(first_query(query, "accountId") or first_query(query, "account") or "").strip(),
        symbol=str(first_query(query, "symbol") or "").upper().strip(),
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
    )


def capital_flow_api_payload(
    query: Dict[str, List[str]],
    *,
    snapshot: Dict[str, object] = None,
    subject_id: str = "",
    quality_only: bool = False,
) -> Dict[str, object]:
    """Read capital movement separately from the decision-lineage flow API."""

    settings = operational_read_settings()
    store = stores.market_time_series_store(settings)
    if quality_only:
        return {
            "contract": "capital-flow-quality-v1",
            **store.capital_flow_quality(safe_int(first_query(query, "days"), 30, 1, 3650)),
        }
    requested_symbols = split_symbols(first_query(query, "symbols") or first_query(query, "symbol") or "")
    if subject_id:
        requested_symbols = [str(subject_id or "").upper().strip()]
    source_snapshot = dict(snapshot or {})
    toss = source_snapshot.get("toss") if isinstance(source_snapshot.get("toss"), dict) else {}
    positions_available = isinstance(toss.get("positions"), list)
    positions = list(toss.get("positions") or []) if positions_available else []
    service = CapitalFlowService(store)
    payload = service.summary(
        symbols=requested_symbols,
        market=str(first_query(query, "market") or ""),
        window_days=safe_int(first_query(query, "windowDays") or first_query(query, "window"), 5, 1, 20),
        observed_after=str(first_query(query, "observedAfter") or ""),
        as_of=str(first_query(query, "asOf") or ""),
        limit=safe_int(first_query(query, "limit"), 10000, 1, 50000),
        positions=positions,
        positions_available=positions_available,
        position_snapshot_as_of=str(source_snapshot.get("generatedAt") or ""),
    )
    payload["readOnly"] = True
    payload["source"] = "capital-flow-observations"
    return payload


def investment_case_api_payload(
    query: Dict[str, List[str]],
    case_id: str = "",
    section: str = "",
) -> Dict[str, object]:
    """Read user-facing cases from persisted decisions without invoking TypeDB."""

    settings = operational_read_settings()
    service = InvestmentCaseQueryService(
        decision_episode_store=stores.investment_decision_episode_store(settings),
        notification_job_store=stores.notification_job_store(settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
        monitor_store=stores.monitor_store(settings) if case_id else None,
        evidence_repository=stores.research_evidence_store(settings) if case_id else None,
        investment_domain_store=stores.investment_domain_store(settings) if case_id else None,
        symbol_repository=stores.symbol_universe_store(settings),
        subject_case_repository=stores.subject_decision_case_store(settings),
    )
    if case_id and section == "history":
        return service.history(
            str(case_id or ""),
            limit=safe_int(first_query(query, "limit"), 30, 1, 200),
        )
    if case_id and section == "trace":
        return service.trace(str(case_id or ""))
    if case_id:
        return service.detail(str(case_id or ""))
    return service.list_cases(
        account_id=str(first_query(query, "accountId") or first_query(query, "account") or "").strip(),
        symbol=str(first_query(query, "symbol") or "").upper().strip(),
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
        include_operator=str(
            first_query(query, "audience") or first_query(query, "includeOperator") or ""
        ).strip().lower() in {"operator", "1", "true", "yes"},
    )


def _investment_model_source_payload() -> Dict[str, object]:
    loaders = {
        "platform": reasoning_engine_platform_status_payload,
        "timeSeries": time_series_platform_status_payload,
        "rulebox": ontology_rulebox_summary_payload,
        "catalog": lambda: ontology_catalog_api_payload("summary", {}),
        "experiments": ontology_experiments_status_payload,
    }
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(loaders), thread_name_prefix="investment-model-read") as executor:
        futures = {key: executor.submit(loader) for key, loader in loaders.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as error:  # noqa: BLE001 - partial model status remains useful.
                results[key] = {}
                errors.append(key + ": " + str(error)[:180])
    payload = investment_model_projection(
        results.get("platform"),
        results.get("rulebox"),
        results.get("catalog"),
        results.get("experiments"),
        runtime_settings(),
        results.get("timeSeries"),
    )
    payload["diagnostics"] = {"partial": bool(errors), "errors": errors}
    return payload


def investment_model_api_payload(force: bool = False) -> Dict[str, object]:
    """Return the last model release immediately while refreshing stale sources."""

    key = "active"
    if force:
        refreshed = INVESTMENT_MODEL_READ_MODEL.refresh(key, _investment_model_source_payload)
        payload = dict(refreshed.get("payload") or {})
        payload["cache"] = {
            "stale": False,
            "ageSeconds": 0,
            "refreshing": False,
            "lastSuccessAt": refreshed.get("lastSuccessAt", ""),
        }
        return payload
    cached = INVESTMENT_MODEL_READ_MODEL.snapshot(key)
    cached_payload = dict(cached.get("payload") or {})
    contract_changed = bool(cached.get("hasData")) and str(
        cached_payload.get("version") or ""
    ) != INVESTMENT_MODEL_VERSION
    if contract_changed:
        refreshed = INVESTMENT_MODEL_READ_MODEL.refresh(key, _investment_model_source_payload)
        payload = dict(refreshed.get("payload") or {})
        if str(payload.get("version") or "") != INVESTMENT_MODEL_VERSION:
            payload = investment_model_projection({}, {}, {}, {}, runtime_settings())
            payload["status"] = "warming"
            payload["diagnostics"] = {
                "partial": True,
                "errors": [
                    str(refreshed.get("lastError") or "투자모델 읽기 계약 갱신을 기다리고 있습니다.")
                ],
            }
        payload["cache"] = {
            "stale": False,
            "ageSeconds": refreshed.get("ageSeconds", 0),
            "refreshing": False,
            "contractMigrated": True,
            "lastSuccessAt": refreshed.get("lastSuccessAt", ""),
        }
        return payload
    if cached.get("hasData"):
        refresh_started = False
        if cached.get("stale"):
            refresh_started = INVESTMENT_MODEL_READ_MODEL.refresh_async(key, _investment_model_source_payload)
        payload = dict(cached.get("payload") or {})
        payload["cache"] = {
            "stale": bool(cached.get("stale")),
            "ageSeconds": cached.get("ageSeconds", 0),
            "refreshing": bool(cached.get("refreshing") or refresh_started),
            "lastSuccessAt": cached.get("lastSuccessAt", ""),
        }
        return payload
    started = INVESTMENT_MODEL_READ_MODEL.refresh_async(key, _investment_model_source_payload)
    current = INVESTMENT_MODEL_READ_MODEL.snapshot(key)
    payload = investment_model_projection({}, {}, {}, {}, runtime_settings())
    payload["status"] = "warming" if started or current.get("refreshing") else "unavailable"
    payload["diagnostics"] = {
        "partial": True,
        "errors": [current.get("lastError")] if current.get("lastError") else [],
    }
    payload["cache"] = {
        "stale": False,
        "ageSeconds": 0,
        "refreshing": bool(started or current.get("refreshing")),
        "lastSuccessAt": "",
    }
    return payload


def save_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    result = ontology_repository_from_settings(runtime_settings()).save_rulebox(payload)
    if isinstance(result, dict) and result:
        ONTOLOGY_RULEBOX_READ_MODEL.store_success("active", result)
    return result


def ontology_language_payload() -> Dict[str, object]:
    settings = runtime_settings()
    registry = investment_language_registry(settings)
    validation = validate_investment_language_registry(registry)
    return {
        "registry": registry,
        "validation": {key: value for key, value in validation.items() if key != "registry"},
        "typeDb": {
            "configured": bool(str(settings.get("typedbAddress") or "").strip()),
            "ontologyBox": "LanguageGovernance",
            "projection": "보편언어 사전은 TypeDB 관리 개념으로 저장되며 투자 규칙과 별도로 버전 관리됩니다.",
        },
    }


def save_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    registry = normalize_investment_language_registry(registry_input)
    registry["updatedAt"] = now()
    registry["source"] = "admin-approved"
    validation = validate_investment_language_registry(registry)
    if not validation.get("valid"):
        raise ValueError("보편언어 사전에 오류가 있어 저장하지 않았습니다: " + "; ".join(
            str(item.get("message") or "") for item in validation.get("errors") or []
        ))
    saved_settings = save_runtime_settings({
        LANGUAGE_REGISTRY_SETTING_KEY: json.dumps(registry, ensure_ascii=False, sort_keys=True),
    })
    type_db_sync: Dict[str, object] = {"status": "skipped", "reason": "활성 TypeDB 규칙을 확인하지 못했습니다."}
    repository = ontology_repository_from_settings(saved_settings)
    try:
        rulebox = repository.rulebox_snapshot()
        active_rules = rulebox.get("rules") if isinstance(rulebox.get("rules"), list) else []
        if active_rules:
            type_db_sync = repository.save_rulebox({"rules": active_rules})
        elif not str(saved_settings.get("typedbAddress") or "").strip():
            type_db_sync = {"status": "disabled", "saved": False, "reason": "TypeDB가 설정되지 않아 로컬 사전만 저장했습니다."}
    except Exception as error:  # noqa: BLE001 - the approved registry remains locally recoverable.
        type_db_sync = {"status": "error", "saved": False, "reason": str(error)[:220]}
    result = ontology_language_payload()
    result["saved"] = True
    result["typeDbSync"] = type_db_sync
    new_domain_event(
        SETTINGS_UPDATED,
        "investment-language",
        {
            "keys": [LANGUAGE_REGISTRY_SETTING_KEY],
            "registryVersion": registry.get("version"),
            "termCount": len(registry.get("terms") or []),
            "typeDbStatus": type_db_sync.get("status"),
        },
    )
    return result


def validate_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    registry_input = body.get("registry") if isinstance(body.get("registry"), dict) else body
    validation = validate_investment_language_registry(registry_input)
    return {key: value for key, value in validation.items() if key != "registry"}


def preview_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return audit_user_facing_investment_text(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def suggest_ontology_language_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    settings = runtime_settings()
    if isinstance(body.get("registry"), dict):
        settings = {**settings, LANGUAGE_REGISTRY_SETTING_KEY: body.get("registry")}
    return propose_investment_language_changes(
        body.get("text") or "",
        settings,
        str(body.get("level") or "absoluteBeginner"),
    )


def run_ontology_rulebox_payload(payload: Dict[str, object]) -> Dict[str, object]:
    values = dict(payload or {})
    world_id = ontology_world_id_from_values(values)
    if not world_id:
        return {
            "status": "world-required",
            "reason": "TypeDB native RuleBox inference requires accountId or an explicit PortfolioWorld worldId.",
            "preservedActiveGeneration": True,
        }
    if world_type_from_id(world_id) != PORTFOLIO_WORLD_TYPE:
        return {
            "status": "portfolio-world-required",
            "reason": "TypeDB native investment inference can run only for a PortfolioWorld, not a shared MarketWorld.",
            "worldId": world_id,
            "preservedActiveGeneration": True,
        }
    values["worldId"] = world_id
    return ontology_repository_from_settings(runtime_settings()).run_rulebox(values)


def ontology_diagnostics_source_payload(
    symbols: List[str],
    limit: int,
    world_id: str,
) -> Dict[str, object]:
    settings = runtime_settings()
    return OntologyDiagnosticsService(
        ontology_repository=ontology_repository_from_settings(settings),
        settings=settings,
        event_log=stores.event_log(settings),
        notification_queue=stores.notification_job_store(settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(settings),
        decision_episode_store=stores.investment_decision_episode_store(settings),
        projection_run_store=stores.ontology_projection_run_store(settings),
        world_projection_outbox=stores.ontology_world_projection_outbox_store(settings),
        inference_detail_outbox=stores.ontology_inference_detail_outbox_store(settings),
        maintenance_state_store=stores.ontology_maintenance_state_store(settings),
        runtime_identity_provider=runtime_identity,
        alert_coverage_provider=lambda: stores.investment_alert_coverage_store(settings).summary(),
    ).status(symbols=symbols, limit=limit, world_id=world_id)


def ontology_diagnostics_cache_key(symbols: List[str], limit: int, world_id: str) -> str:
    return json.dumps({
        "symbols": sorted(str(item or "").upper() for item in symbols or []),
        "limit": int(limit or 0),
        "worldId": str(world_id or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ontology_diagnostics_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    if request_bool(first_query(query, "quick"), False):
        return {
            "status": "deferred",
            "mode": "quick",
            "generatedAt": now(),
            "graphStore": "typedb",
            "reason": "초기 화면은 TypeDB 전체 진단을 실행하지 않습니다. 상세 진단 버튼에서 저장소 행과 추론 상태를 확인합니다.",
            "detailAvailable": True,
        }
    symbols = [
        item.strip().upper()
        for item in str(first_query(query, "symbols") or first_query(query, "symbol") or "").split(",")
        if item.strip()
    ]
    limit = max(1, min(500, int(first_query(query, "limit") or 80)))
    world_id = ontology_world_id_from_query(query)
    key = ontology_diagnostics_cache_key(symbols, limit, world_id)
    cached = ONTOLOGY_DIAGNOSTICS_READ_MODEL.snapshot(key)
    refresh_requested = request_bool(first_query(query, "refresh"), False)
    refresh_started = False
    if refresh_requested or cached.get("stale") or not cached.get("hasData"):
        refresh_started = ONTOLOGY_DIAGNOSTICS_READ_MODEL.refresh_async(
            key,
            lambda: ontology_diagnostics_source_payload(symbols, limit, world_id),
        )
    refreshing = bool(cached.get("refreshing") or refresh_started)
    cache_payload = {
        "stale": bool(cached.get("stale")),
        "ageSeconds": int(cached.get("ageSeconds") or 0),
        "refreshing": refreshing,
        "lastSuccessAt": str(cached.get("lastSuccessAt") or ""),
        "lastAttemptAt": str(cached.get("lastAttemptAt") or ""),
        "lastError": str(cached.get("lastError") or ""),
        "retryAfterSeconds": int(cached.get("retryAfterSeconds") or 0),
    }
    if cached.get("hasData"):
        payload = dict(cached.get("payload") or {})
        payload["cache"] = cache_payload
        return payload
    return {
        "contract": "typedb-ontology-diagnostics-v1",
        "status": "warming" if refreshing else "unavailable",
        "mode": "stale-while-revalidate",
        "generatedAt": now(),
        "activeGraphStore": "typedb",
        "worldId": world_id,
        "reason": (
            "TypeDB 상세 진단을 백그라운드에서 읽고 있습니다. 화면은 완료를 기다리지 않고 자동으로 갱신됩니다."
            if refreshing
            else "TypeDB 상세 진단을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ),
        "detailAvailable": True,
        "cache": cache_payload,
    }


def ontology_inference_ledger_cache_key(symbols: List[str], limit: int, world_id: str) -> str:
    return json.dumps({
        "symbols": sorted(str(item or "").upper() for item in symbols or []),
        "limit": int(limit or 0),
        "worldId": str(world_id or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ontology_inference_graph_read_model(
    symbols: List[str],
    limit: int,
    world_id: str,
) -> Dict[str, object]:
    settings = runtime_settings(fast_operational_read=True)
    repo = ontology_repository_from_settings(settings)
    errors = []
    try:
        rulebox = repo.rulebox_snapshot() if hasattr(repo, "rulebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - a partial graph snapshot can still be retained.
        errors.append("RuleBox: " + str(error)[:220])
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = ontology_repository_world_call(
            repo,
            "inferencebox_snapshot",
            symbols=symbols,
            limit=limit,
            world_id=world_id,
        ) if hasattr(repo, "inferencebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - the prior durable read model remains usable.
        errors.append("InferenceBox: " + str(error)[:220])
        inferencebox = {
            "status": "error",
            "reason": str(error)[:220],
            "graphStore": getattr(repo, "store_key", "typedb"),
            "source": "typedbInferenceBox",
            "entities": [],
            "relations": [],
            "traces": [],
        }
    rulebox_usable = bool((rulebox or {}).get("rules")) or str((rulebox or {}).get("status") or "").lower() in {"ok", "ready", "current"}
    inference_usable = bool(
        (inferencebox or {}).get("entities")
        or (inferencebox or {}).get("relations")
        or (inferencebox or {}).get("traces")
    ) or str((inferencebox or {}).get("status") or "").lower() in {"ok", "ready", "current"}
    if not rulebox_usable and not inference_usable:
        raise RuntimeError("; ".join(errors) or "TypeDB inference read model is unavailable")
    return {
        "rulebox": rulebox,
        "inferencebox": inferencebox,
        "generatedAt": now(),
        "worldId": world_id,
    }


def compact_reasoning_stage_detail(stage_key: str, detail: object) -> Dict[str, object]:
    value = detail if isinstance(detail, dict) else {}
    key = str(stage_key or "")
    if key == "source-fact-capture":
        return {
            "changedFieldsBySymbol": dict(value.get("changedFieldsBySymbol") or {}),
            "factTypes": list(value.get("factTypes") or []),
        }
    if key == "abox-scope-selection":
        return {
            "selectedScopeCount": value.get("selectedScopeCount"),
            "deferredScopeCount": value.get("deferredScopeCount"),
            "factSlotFamilies": list(value.get("factSlotFamilies") or []),
            "selectedScopes": [{
                field: scope.get(field)
                for field in ("symbol", "scopeFamily", "scopeId", "reasons")
                if field in scope
            } for scope in value.get("selectedScopes") or [] if isinstance(scope, dict)],
        }
    if key == "abox-persistence":
        return {
            "scopeCount": value.get("scopeCount"),
            "scopes": [{
                field: scope.get(field)
                for field in ("symbol", "scopeFamily", "scopeId", "requested", "inserted", "reused")
                if field in scope
            } for scope in value.get("scopes") or [] if isinstance(scope, dict)],
        }
    if key == "rulebox-selection":
        return {
            field: value.get(field)
            for field in ("candidateRuleCount", "executedRuleCount", "deferredRuleCount")
            if field in value
        }
    if key.startswith("runtime:"):
        return {
            field: value.get(field)
            for field in ("runtimeMetric", "budgetMs", "ratio", "withinBudget")
            if field in value
        }
    if key == "performance-contract":
        return {
            field: value.get(field)
            for field in (
                "version", "withinBudget", "bottleneckStage", "bottleneckRatio",
                "violations",
            )
            if field in value
        }
    return {}


def compact_reasoning_execution_history(history: object) -> Dict[str, object]:
    payload = dict(history or {}) if isinstance(history, dict) else {}
    compact_runs = []
    for raw_run in payload.get("runs") or []:
        if not isinstance(raw_run, dict):
            continue
        stages = []
        for raw_stage in raw_run.get("stages") or []:
            if not isinstance(raw_stage, dict):
                continue
            stage_key = str(raw_stage.get("stageKey") or "")
            stages.append({
                field: raw_stage.get(field)
                for field in (
                    "stageKey", "status", "durationMs",
                )
                if field in raw_stage
            } | {"detail": compact_reasoning_stage_detail(stage_key, raw_stage.get("detail"))})
        rules = [{
            field: raw_rule.get(field)
            for field in (
                "ruleId", "status", "selectedReason", "durationMs", "failureReason",
            )
            if field in raw_rule
        } for raw_rule in raw_run.get("rules") or [] if isinstance(raw_rule, dict)]
        compact_runs.append({
            field: raw_run.get(field)
            for field in (
                "runId", "worldId", "accountId", "inferenceGenerationId", "lane", "updatedAt",
            )
            if field in raw_run
        } | {"stages": stages, "rules": rules})
    payload["runs"] = compact_runs
    payload["detailLevel"] = "summary"
    payload["fullDetailAvailable"] = True
    return payload


def compact_rule_audit(audit: object) -> Dict[str, object]:
    payload = dict(audit or {}) if isinstance(audit, dict) else {}
    payload["rules"] = [{
        **{
            field: rule.get(field)
            for field in (
                "ruleId", "label", "status", "enabled", "assessmentScope",
                "lifecycleClass", "evaluationGrain", "ownerWorld", "executionCadence",
                "incrementalEligible", "triggerEventClasses", "executionUnit",
                "sampleCount", "matchedCount", "failureCount",
                "averageDurationMs", "p95DurationMs", "maxDurationMs", "reviewReasons",
            )
            if field in rule
        },
        "executionProfile": {
            "executionStage": (
                rule.get("executionProfile").get("executionStage")
                if isinstance(rule.get("executionProfile"), dict)
                else None
            ),
        },
    } for rule in payload.get("rules") or [] if isinstance(rule, dict)]
    payload["detailLevel"] = "summary"
    payload["fullDetailAvailable"] = True
    return payload


def ontology_inference_ledger_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    from ..domain.ontology_rule_audit import rule_audit_payload

    settings = operational_read_settings()
    symbols = ontology_audit_symbols(query)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    world_id = ontology_world_id_from_query(query)
    cache_key = ontology_inference_ledger_cache_key(symbols, limit, world_id)
    loader = lambda: ontology_inference_graph_read_model(symbols, limit, world_id)
    direct = request_bool(first_query(query, "direct"), False)
    if direct:
        read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.refresh(cache_key, loader)
    else:
        # HTTP reads must never run a TypeDB graph scan inside the web process.
        # A direct diagnostic can refresh the durable snapshot explicitly;
        # ordinary screens keep serving the last good graph plus MySQL history.
        read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.snapshot(cache_key)
        if (not read_model.get("hasData") or read_model.get("stale")) and not read_model.get("retryAfterSeconds"):
            ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.refresh_async(cache_key, loader)
            read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.snapshot(cache_key)
    graph_payload = read_model.get("payload") if isinstance(read_model.get("payload"), dict) else {}
    rulebox = graph_payload.get("rulebox") if isinstance(graph_payload.get("rulebox"), dict) else {
        "status": "deferred",
        "reason": str(read_model.get("lastError") or "TypeDB snapshot refresh is pending."),
        "rules": [],
    }
    inferencebox = graph_payload.get("inferencebox") if isinstance(graph_payload.get("inferencebox"), dict) else {
        "status": "deferred",
        "reason": str(read_model.get("lastError") or "TypeDB snapshot refresh is pending."),
        "graphStore": "typedb",
        "source": "persistentReadModel",
        "entities": [],
        "relations": [],
        "traces": [],
    }
    payload = inference_trace_ledger_payload(inferencebox, rulebox=rulebox, symbols=symbols, limit=limit)
    payload["ruleboxStatus"] = rulebox.get("status")
    payload["ruleboxReason"] = rulebox.get("reason")
    payload["worldId"] = world_id
    try:
        execution_store = stores.ontology_projection_run_store(settings)
        execution_history = execution_store.execution_trace(
            run_id=str(first_query(query, "runId") or ""),
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            limit=safe_int(first_query(query, "runLimit"), 12, 1, 50),
        )
        if str(first_query(query, "historyDetail") or "summary").strip().lower() != "full":
            execution_history = compact_reasoning_execution_history(execution_history)
        elif isinstance(execution_history, dict):
            execution_history["detailLevel"] = "full"
        payload["executionHistory"] = execution_history
        payload["ruleRuntimeSummary"] = execution_store.rule_runtime_summary(
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            limit=safe_int(first_query(query, "ruleSampleLimit"), 500, 100, 10000),
        )
        payload["ruleResultSlots"] = execution_store.rule_result_slot_summary(
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            symbols=symbols,
            limit=safe_int(first_query(query, "slotLimit"), 500, 100, 10000),
            execution_namespace_id=str(first_query(query, "executionNamespaceId") or ""),
        )
        payload["ruleAudit"] = rule_audit_payload(
            rulebox.get("rules") or [],
            payload["ruleRuntimeSummary"],
        )
        if str(first_query(query, "auditDetail") or "summary").strip().lower() != "full":
            payload["ruleAudit"] = compact_rule_audit(payload["ruleAudit"])
        elif isinstance(payload["ruleAudit"], dict):
            payload["ruleAudit"]["detailLevel"] = "full"
    except Exception as error:  # noqa: BLE001 - active InferenceBox trace remains readable.
        payload["executionHistory"] = {
            "status": "error",
            "reason": str(error)[:220],
            "runCount": 0,
            "runs": [],
        }
        payload["ruleRuntimeSummary"] = {
            "status": "error",
            "reason": str(error)[:220],
            "sampleCount": 0,
            "ruleCount": 0,
            "rules": [],
        }
        payload["ruleResultSlots"] = {
            "status": "error",
            "reason": str(error)[:220],
            "slotCount": 0,
            "symbolCount": 0,
            "symbols": [],
        }
        payload["ruleAudit"] = rule_audit_payload(rulebox.get("rules") or [], {})
        if str(first_query(query, "auditDetail") or "summary").strip().lower() != "full":
            payload["ruleAudit"] = compact_rule_audit(payload["ruleAudit"])
    operational_count = sum([
        int((payload.get("executionHistory") or {}).get("runCount") or 0),
        int((payload.get("ruleRuntimeSummary") or {}).get("sampleCount") or 0),
        int((payload.get("ruleResultSlots") or {}).get("slotCount") or 0),
    ])
    has_graph = bool(read_model.get("hasData"))
    stale_graph = bool(read_model.get("stale"))
    refreshing = bool(read_model.get("refreshing"))
    usable = has_graph or operational_count > 0
    status = (
        "stale" if has_graph and stale_graph
        else "ok" if has_graph
        else "degraded" if operational_count > 0
        else "refreshing" if refreshing
        else "unavailable"
    )
    dependency_status = (
        "stale" if has_graph and stale_graph
        else "available" if has_graph
        else "refreshing" if refreshing
        else "unavailable"
    )
    payload.update({
        "status": status,
        "usable": usable,
        "retryable": not has_graph,
        "generatedAt": now(),
        "dataFreshness": {
            "status": "stale" if stale_graph else "fresh" if has_graph else "unavailable",
            "ageSeconds": int(read_model.get("ageSeconds") or 0),
            "lastSuccessAt": str(read_model.get("lastSuccessAt") or ""),
            "source": "persistent-read-model" if has_graph else "mysql-execution-history",
        },
        "dependencyStatus": {
            "typedb": {
                "status": dependency_status,
                "refreshing": refreshing,
                "lastAttemptAt": str(read_model.get("lastAttemptAt") or ""),
                "lastError": str(read_model.get("lastError") or ""),
                "retryAfterSeconds": int(read_model.get("retryAfterSeconds") or 0),
                "refreshMode": "direct-diagnostic" if direct else "stale-while-revalidate",
            }
        },
    })
    if direct and not usable:
        payload["error"] = str(read_model.get("lastError") or "TypeDB inference API is unavailable.")
    return payload


ONTOLOGY_AUDIT_BOXES = ["TBox", "ABox", "RuleBox", "RuleBoxGovernance", "LanguageGovernance", "InferenceBox"]
ONTOLOGY_AUDIT_SECTION_LABELS = {
    "tbox": ("TBox", "스키마와 관계 타입"),
    "abox": ("ABox", "현재 실체 데이터"),
    "rulebox": ("RuleBox", "운영 규칙과 후보"),
    "inferencebox": ("InferenceBox", "세대별 추론 결과"),
    "language": ("LanguageGovernance", "보편언어 사전과 승인 상태"),
    "evidence": ("Evidence Trace", "근거, 믿음, 의견, 실행 계획"),
    "sync": ("TypeDB Sync", "동기화와 저장소 상태"),
}
ONTOLOGY_AUDIT_EVIDENCE_KINDS = {
    "evidence",
    "research-evidence",
    "belief",
    "investment-opinion",
    "opinion",
    "active-investment-opinion",
    "execution-plan",
    "reasoning-card",
    "inference-trace",
    "insight",
    "data-quality",
    "data-freshness",
    "provenance",
    "source-reliability",
    "missing-data",
}


def safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def ontology_world_id_from_values(values: Dict[str, object]) -> str:
    """Resolve an explicit world or an account query to PortfolioWorld.

    API callers can use a stable ``worldId`` once they have it, while existing
    account-oriented UI calls continue to select the same isolated world by
    supplying ``accountId`` and an optional ``tenantId``.
    """
    payload = values if isinstance(values, dict) else {}
    explicit = str(
        payload.get("worldId")
        or payload.get("ontologyWorldId")
        or payload.get("world_id")
        or ""
    ).strip()
    if explicit:
        return explicit
    account_id = str(payload.get("accountId") or payload.get("account_id") or "").strip()
    if not account_id:
        return ""
    tenant_id = str(payload.get("tenantId") or payload.get("tenant_id") or "").strip()
    return portfolio_world_id(account_id, tenant_id)


def ontology_world_id_from_query(query: Dict[str, List[str]]) -> str:
    return ontology_world_id_from_values({
        "worldId": first_query(query, "worldId") or first_query(query, "ontologyWorldId"),
        "accountId": first_query(query, "accountId") or first_query(query, "account"),
        "tenantId": first_query(query, "tenantId") or first_query(query, "tenant"),
    })


def ontology_repository_world_call(repo, method_name: str, *args, world_id: str = "", **kwargs):
    method = getattr(repo, method_name, None)
    if not callable(method):
        raise AttributeError(method_name + " is unavailable")
    if not str(world_id or "").strip():
        return method(*args, **kwargs)
    try:
        return method(*args, world_id=str(world_id), **kwargs)
    except TypeError as error:
        if "unexpected keyword" not in str(error) and "world_id" not in str(error):
            raise
        return method(*args, **kwargs)


def ontology_audit_symbols(query: Dict[str, List[str]]) -> List[str]:
    raw = first_query(query, "symbols") or first_query(query, "symbol")
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def ontology_audit_row_text(row: Dict[str, object]) -> str:
    try:
        return json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    except (TypeError, ValueError):
        return str(row or "").lower()


def ontology_audit_row_payload(row: Dict[str, object], row_type: str) -> Dict[str, object]:
    raw = dict(row or {})
    label = str(raw.get("label") or raw.get("title") or raw.get("id") or raw.get("relationType") or raw.get("type") or row_type)
    kind = str(raw.get("kind") or raw.get("nodeKind") or raw.get("relationType") or raw.get("type") or row_type)
    box = str(raw.get("ontologyBox") or raw.get("box") or "")
    source = str(raw.get("sourceLabel") or raw.get("source") or "")
    target = str(raw.get("targetLabel") or raw.get("target") or "")
    relation_type = str(raw.get("relationType") or raw.get("type") or "")
    stable_source = json.dumps({
        "type": row_type,
        "box": box,
        "id": raw.get("id"),
        "source": raw.get("source"),
        "target": raw.get("target"),
        "relationType": relation_type,
        "label": label,
    }, ensure_ascii=False, sort_keys=True)
    return {
        "key": hashlib.sha1(stable_source.encode("utf-8")).hexdigest()[:14],
        "rowType": row_type,
        "id": str(raw.get("id") or ""),
        "label": label,
        "kind": kind,
        "box": box,
        "relationType": relation_type,
        "source": source,
        "target": target,
        "symbol": str(raw.get("symbol") or ""),
        "ruleId": str(raw.get("ruleId") or raw.get("sourceRuleId") or raw.get("semanticRuleId") or ""),
        "status": str(raw.get("status") or ""),
        "updatedAt": str(raw.get("updatedAt") or raw.get("createdAt") or ""),
        "weight": raw.get("weight"),
        "raw": raw,
    }


def ontology_audit_filtered_rows(rows: List[Dict[str, object]], search: str, symbols: List[str]) -> List[Dict[str, object]]:
    needle = str(search or "").strip().lower()
    clean_symbols = [item.upper() for item in symbols or [] if item]
    result = []
    for row in rows or []:
        haystack = ontology_audit_row_text(row)
        if needle and needle not in haystack:
            continue
        if clean_symbols and not any(symbol in haystack.upper() for symbol in clean_symbols):
            continue
        result.append(row)
    return result


def ontology_audit_section_payload(
    section_id: str,
    rows: List[Dict[str, object]],
    limit: int,
    offset: int,
    search: str,
    symbols: List[str],
) -> Dict[str, object]:
    label, description = ONTOLOGY_AUDIT_SECTION_LABELS.get(section_id, (section_id, ""))
    filtered = ontology_audit_filtered_rows(rows, search, symbols)
    paged = filtered[offset: offset + limit]
    return {
        "id": section_id,
        "label": label,
        "description": description,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "hasMore": offset + limit < len(filtered),
        "entityCount": len([row for row in filtered if row.get("rowType") == "entity"]),
        "relationCount": len([row for row in filtered if row.get("rowType") == "relation"]),
        "rows": paged,
    }


def ontology_audit_rulebox_rows(rulebox: Dict[str, object], graph_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = list(graph_rows or [])
    if rows:
        return rows
    for rule in rulebox.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        rows.append(ontology_audit_row_payload({
            **rule,
            "id": rule.get("id") or rule.get("rule_id") or rule.get("name"),
            "label": rule.get("label") or rule.get("title") or rule.get("id") or "RuleBox rule",
            "kind": "rule",
            "ontologyBox": "RuleBox",
            "status": "fallback" if rulebox.get("defaultsFallbackUsed") else str(rule.get("status") or "active"),
        }, "rule"))
    return rows


def ontology_audit_sync_rows(
    repo,
    tbox: Dict[str, object],
    rulebox: Dict[str, object],
    inferencebox: Dict[str, object],
    diagnostics: Dict[str, object],
    world_id: str = "",
    include_generation_records: bool = True,
) -> List[Dict[str, object]]:
    rows = [
        ontology_audit_row_payload({
            "id": "typedb.tbox",
            "label": "TBox metadata",
            "kind": "sync-status",
            "ontologyBox": "TBox",
            "status": tbox.get("status") or ("ok" if tbox.get("configured") else "disabled"),
            "updatedAt": tbox.get("updatedAt") or "",
            "source": tbox.get("source") or tbox.get("storeSource") or "",
            "raw": tbox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.rulebox",
            "label": "RuleBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "RuleBox",
            "status": rulebox.get("status") or ("ok" if rulebox.get("configured") else "disabled"),
            "updatedAt": rulebox.get("updatedAt") or "",
            "ruleCount": rulebox.get("ruleCount"),
            "raw": rulebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.inferencebox",
            "label": "InferenceBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "InferenceBox",
            "status": inferencebox.get("status") or ("ok" if inferencebox.get("configured") else "disabled"),
            "updatedAt": inferencebox.get("updatedAt") or "",
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "raw": inferencebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "ontology.diagnostics",
            "label": "Ontology diagnostics",
            "kind": "diagnostics",
            "ontologyBox": "Runtime",
            "status": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "updatedAt": diagnostics.get("generatedAt") or "",
            "raw": diagnostics,
        }, "status"),
    ]
    # The default audit page is deliberately a bounded summary.  Reading every
    # historical InferenceBox node and relation just to show the latest 20
    # generation labels can dominate page load as the graph grows.  The
    # detailed sync section still opts in to this durable history read.
    if include_generation_records and hasattr(repo, "read_inference_generation_records"):
        try:
            for index, generation in enumerate(ontology_repository_world_call(
                repo,
                "read_inference_generation_records",
                world_id=world_id,
            )[:20]):
                rows.append(ontology_audit_row_payload({
                    **generation,
                    "id": generation.get("generationId") or generation.get("snapshotId") or ("generation-" + str(index + 1)),
                    "label": "Inference generation " + str(index + 1),
                    "kind": "inference-generation",
                    "ontologyBox": "InferenceBox",
                    "status": "materialized",
                    "updatedAt": generation.get("updatedAt") or "",
                }, "status"))
        except Exception as error:  # noqa: BLE001 - audit UI should expose read errors instead of failing.
            rows.append(ontology_audit_row_payload({
                "id": "typedb.inference-generation.error",
                "label": "Inference generation read error",
                "kind": "sync-error",
                "ontologyBox": "InferenceBox",
                "status": "error",
                "reason": str(error)[:220],
            }, "status"))
    return rows


def ontology_audit_payload(query: Dict[str, List[str]], requested_section: str = "") -> Dict[str, object]:
    settings = runtime_settings()
    repo = ontology_repository_from_settings(settings)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    offset = safe_int(first_query(query, "offset"), 0, 0, 100000)
    search = first_query(query, "q") or first_query(query, "query")
    symbols = ontology_audit_symbols(query)
    world_id = ontology_world_id_from_query(query)
    section_filter = str(requested_section or first_query(query, "section") or "").strip().lower()
    if section_filter == "all":
        section_filter = ""
    compact_all = not section_filter
    fast_compact_summary = compact_all and not search and not symbols
    section_ids = [section_filter] if section_filter in ONTOLOGY_AUDIT_SECTION_LABELS else list(ONTOLOGY_AUDIT_SECTION_LABELS.keys())
    section_box_map = {
        "tbox": ["TBox"],
        "abox": ["ABox"],
        "rulebox": ["RuleBox", "RuleBoxGovernance"],
        "inferencebox": ["InferenceBox"],
        "evidence": ["ABox", "InferenceBox"],
        "sync": [],
    }
    read_boxes = sorted({
        box
        for section_id in section_ids
        for box in section_box_map.get(section_id, ONTOLOGY_AUDIT_BOXES)
    })
    graph_entities: List[Dict[str, object]] = []
    graph_relations: List[Dict[str, object]] = []
    graph_error = ""
    if fast_compact_summary:
        graph_error = ""
    elif read_boxes and hasattr(repo, "read_entity_rows") and hasattr(repo, "read_relation_rows"):
        try:
            if compact_all:
                sample_limit = max(5, min(80, limit))
                for box in read_boxes:
                    graph_entities.extend([
                        ontology_audit_row_payload(row, "entity")
                        for row in ontology_repository_world_call(
                            repo,
                            "read_entity_rows",
                            [box],
                            sample_limit,
                            world_id=world_id,
                        )
                    ])
                    graph_relations.extend([
                        ontology_audit_row_payload(row, "relation")
                        for row in ontology_repository_world_call(
                            repo,
                            "read_relation_rows",
                            [box],
                            sample_limit,
                            world_id=world_id,
                        )
                    ])
            else:
                graph_entities = [
                    ontology_audit_row_payload(row, "entity")
                    for row in ontology_repository_world_call(
                        repo,
                        "read_entity_rows",
                        read_boxes,
                        world_id=world_id,
                    )
                ]
                graph_relations = [
                    ontology_audit_row_payload(row, "relation")
                    for row in ontology_repository_world_call(
                        repo,
                        "read_relation_rows",
                        read_boxes,
                        world_id=world_id,
                    )
                ]
        except Exception as error:  # noqa: BLE001 - admin audit must degrade gracefully.
            graph_error = str(error)[:240]
    elif read_boxes:
        graph_error = "TypeDB row reader is not available for this graph store."

    graph_rows = graph_entities + graph_relations
    by_box = {}
    for row in graph_rows:
        by_box.setdefault(str(row.get("box") or ""), []).append(row)

    tbox_metadata: Dict[str, object] = {}
    rulebox: Dict[str, object] = {}
    inferencebox: Dict[str, object] = {}
    diagnostics: Dict[str, object] = {}
    try:
        tbox_metadata = (
            {"status": "sampled", "source": "audit-sample", "configured": bool(getattr(repo, "address", ""))}
            if compact_all
            else repo.active_tbox_metadata()
            if ("tbox" in section_ids or "sync" in section_ids) and hasattr(repo, "active_tbox_metadata")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        tbox_metadata = {"status": "error", "reason": str(error)[:220]}
    try:
        rulebox = (
            {"status": "sampled", "source": "audit-sample", "rules": []}
            if compact_all
            else repo.rulebox_snapshot()
            if ("rulebox" in section_ids or "sync" in section_ids) and hasattr(repo, "rulebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = (
            {"status": "sampled", "source": "audit-sample", "entities": [], "relations": [], "traces": []}
            if compact_all
            else ontology_repository_world_call(
                repo,
                "inferencebox_snapshot",
                symbols=symbols,
                limit=min(300, max(80, limit)),
                world_id=world_id,
            )
            if ("inferencebox" in section_ids or "sync" in section_ids) and hasattr(repo, "inferencebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        inferencebox = {"status": "error", "reason": str(error)[:220], "entities": [], "relations": [], "traces": []}
    try:
        diagnostics = (
            {"status": "sampled", "reason": "기본 감사 화면은 빠른 샘플만 읽고, 상세 진단은 /api/ontology/audit/sync에서 실행합니다."}
            if compact_all and "sync" in section_ids
            else OntologyDiagnosticsService(
                ontology_repository=repo,
                settings=settings,
                event_log=stores.event_log(settings),
                notification_queue=stores.notification_job_store(settings),
                strategy_proposal_service=build_investment_strategy_proposal_service(settings),
                decision_episode_store=stores.investment_decision_episode_store(settings),
                projection_run_store=stores.ontology_projection_run_store(settings),
                world_projection_outbox=stores.ontology_world_projection_outbox_store(settings),
                inference_detail_outbox=stores.ontology_inference_detail_outbox_store(settings),
                maintenance_state_store=stores.ontology_maintenance_state_store(settings),
                runtime_identity_provider=runtime_identity,
                alert_coverage_provider=lambda: stores.investment_alert_coverage_store(settings).summary(),
            ).status(
                symbols=symbols,
                limit=min(300, max(80, limit)),
                world_id=world_id,
            ) if "sync" in section_ids else {}
        )
    except Exception as error:  # noqa: BLE001
        diagnostics = {"status": "error", "reason": str(error)[:220]}

    inference_rows = by_box.get("InferenceBox", [])
    if not inference_rows:
        inference_rows = [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "entity")
            for row in (inferencebox.get("entities") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "relation")
            for row in (inferencebox.get("relations") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox", "kind": row.get("kind") or "inference-trace"}, "trace")
            for row in (inferencebox.get("traces") or [])
            if isinstance(row, dict)
        ]

    evidence_rows = [
        row for row in graph_rows
        if str(row.get("kind") or "").lower() in ONTOLOGY_AUDIT_EVIDENCE_KINDS
        or any(token in ontology_audit_row_text(row) for token in ["evidence", "belief", "opinion", "executionplan", "reasoningcard"])
    ]

    sections = {}
    if "tbox" in section_ids:
        sections["tbox"] = ontology_audit_section_payload("tbox", by_box.get("TBox", []), limit, offset, search, symbols)
    if "abox" in section_ids:
        sections["abox"] = ontology_audit_section_payload("abox", by_box.get("ABox", []), limit, offset, search, symbols)
    if "rulebox" in section_ids:
        sections["rulebox"] = ontology_audit_section_payload(
            "rulebox",
            ontology_audit_rulebox_rows(rulebox, by_box.get("RuleBox", []) + by_box.get("RuleBoxGovernance", [])),
            limit,
            offset,
            search,
            symbols,
        )
    if "inferencebox" in section_ids:
        sections["inferencebox"] = ontology_audit_section_payload("inferencebox", inference_rows, limit, offset, search, symbols)
    if "evidence" in section_ids:
        sections["evidence"] = ontology_audit_section_payload("evidence", evidence_rows, limit, offset, search, symbols)
    if "sync" in section_ids:
        sections["sync"] = ontology_audit_section_payload(
            "sync",
            ontology_audit_sync_rows(
                repo,
                tbox_metadata,
                rulebox,
                inferencebox,
                diagnostics,
                world_id=world_id,
                include_generation_records=not fast_compact_summary,
            ),
            limit,
            offset,
            search,
            symbols,
        )

    totals = {key: value.get("total", 0) for key, value in sections.items()}
    status = "error" if graph_error else ("sampled" if fast_compact_summary else "ok")
    if not getattr(repo, "address", "") and all((sections.get(key) or {}).get("total", 0) == 0 for key in ["tbox", "abox", "inferencebox"] if key in sections):
        status = "disabled"
    return {
        "generatedAt": now(),
        "status": status,
        "graphStore": getattr(repo, "store_key", "typedb"),
        "storeLabel": getattr(repo, "store_label", "TypeDB"),
        "worldId": world_id,
        "configured": bool(getattr(repo, "address", "") or rulebox.get("configured") or tbox_metadata.get("configured")),
        "error": graph_error,
        "query": {
            "limit": limit,
            "offset": offset,
            "q": search,
            "symbols": symbols,
            "worldId": world_id,
            "section": section_filter or "all",
        },
        "summaryMode": "sampled" if fast_compact_summary else "full",
        "summary": {
            "sectionTotals": totals if not fast_compact_summary else {},
            "graphRowCount": len(graph_rows) if not fast_compact_summary else None,
            "entityCount": len(graph_entities) if not fast_compact_summary else None,
            "relationCount": len(graph_relations) if not fast_compact_summary else None,
            "ruleCount": rulebox.get("ruleCount") or len(rulebox.get("rules") or []) or (sections.get("rulebox") or {}).get("total", 0),
            "inferenceRelationCount": inferencebox.get("relationCount") or (sections.get("inferencebox") or {}).get("relationCount", 0),
            "inferenceTraceCount": inferencebox.get("traceCount") or 0,
            "diagnosticsStatus": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "tboxStatus": tbox_metadata.get("status") or "",
            "ruleboxStatus": rulebox.get("status") or "",
            "inferenceboxStatus": inferencebox.get("status") or "",
        },
        "sections": sections,
        "tbox": tbox_metadata,
        "rulebox": {
            "status": rulebox.get("status"),
            "ruleCount": rulebox.get("ruleCount"),
            "conditionCount": rulebox.get("conditionCount"),
            "derivationCount": rulebox.get("derivationCount"),
            "defaultsFallbackUsed": rulebox.get("defaultsFallbackUsed"),
            "versionCount": rulebox.get("versionCount"),
            "source": rulebox.get("source"),
        },
        "inferencebox": {
            "status": inferencebox.get("status"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "reasoningMode": inferencebox.get("reasoningMode"),
            "source": inferencebox.get("source"),
        },
        "diagnostics": diagnostics,
    }


def propose_ontology_rule_candidates_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "manual"),
        account_id=str(body.get("accountId") or body.get("account_id") or ""),
        tenant_id=str(body.get("tenantId") or body.get("tenant_id") or ""),
    )
    snapshot = ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()
    result["rulebox"] = snapshot
    return result


def seed_ontology_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).seed_ontology(payload)


def ontology_lab_service():
    return build_ontology_lab_service(runtime_settings())


def hypothesis_development_service():
    return build_hypothesis_development_service(runtime_settings())


def hypothesis_development_cases_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    try:
        limit = int(first_query(query, "limit") or 100)
    except ValueError:
        limit = 100
    return hypothesis_development_service().list(
        status=first_query(query, "status"),
        symbol=first_query(query, "symbol"),
        limit=max(1, min(500, limit)),
    )


def hypothesis_development_case_payload(case_id: str) -> Dict[str, object]:
    return hypothesis_development_service().report(case_id)


def process_hypothesis_development_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = dict(payload or {})
    if str(body.get("caseId") or ""):
        return hypothesis_development_service().process(str(body.get("caseId")))
    return hypothesis_development_service().process_pending(limit=max(1, min(20, int(body.get("limit") or 5))))


def approve_hypothesis_development_payload(case_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    service = hypothesis_development_service()
    report = service.report(case_id)
    case = report.get("case") if isinstance(report.get("case"), dict) else {}
    if str(case.get("status") or "") != "approval-required":
        return {
            "status": "not-ready",
            "reason": "hypothesis-development-case-not-validated",
            "case": case,
        }
    experiment_id = str(case.get("experimentId") or "")
    body = {
        **dict(payload or {}),
        "runRulebox": True,
        "rollbackOnInferenceFailure": True,
        "reviewApproved": True,
        "reviewedBy": str((payload or {}).get("reviewedBy") or "web-main"),
        "reviewReason": str((payload or {}).get("reviewReason") or "검증 탭에서 자동 검증 완료 가설의 운영 반영 승인"),
    }
    result = ontology_lab_service().apply_recommendations(experiment_id, body)
    development = service.mark_deployed(case_id, result.get("application") or result)
    return {"status": development.get("status") or result.get("status"), "development": development, "application": result}


def list_ontology_experiments_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 8)))
    offset = max(0, int(first_query(query, "offset") or 0))
    summary = not request_bool(first_query(query, "detail"), False)
    return cached_api_payload(
        ONTOLOGY_EXPERIMENT_LIST_READ_MODEL,
        "|".join([str(limit), str(offset), "summary" if summary else "detail"]),
        lambda: ontology_lab_service().list(limit=limit, offset=offset, summary=summary),
        force=request_bool(first_query(query, "refresh"), False),
    )


def _ontology_experiments_status_source_payload() -> Dict[str, object]:
    return ontology_lab_service().status()


def ontology_experiments_status_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        ONTOLOGY_EXPERIMENT_STATUS_READ_MODEL,
        "status",
        _ontology_experiments_status_source_payload,
        force=force,
        blocking_first_load=False,
    )


def hypothesis_templates_api_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        HYPOTHESIS_TEMPLATE_READ_MODEL,
        "active",
        lambda: build_investment_brain_service(operational_read_settings()).hypothesis_templates(),
        force=force,
    )


def hypothesis_policy_versions_api_payload(limit: int = 40, force: bool = False) -> Dict[str, object]:
    safe_limit_value = max(1, min(100, int(limit or 40)))
    return cached_api_payload(
        HYPOTHESIS_POLICY_VERSION_READ_MODEL,
        str(safe_limit_value),
        lambda: build_investment_brain_service(operational_read_settings()).hypothesis_policy_versions(
            limit=safe_limit_value,
        ),
        force=force,
    )


def ontology_reasoning_status_payload() -> Dict[str, object]:
    """Expose scheduler-only queue health without running a TypeDB cycle."""
    try:
        # The full runner status evaluates account priority and TypeDB health.
        # The settings screen needs only queue liveness, so use the bounded
        # read probe instead of constructing the operational worker.
        configured = operational_read_settings()
        payload = dict(build_ontology_reasoning_queue_probe(configured)() or {})
        control = stores.reasoning_engine_registry_store(configured).control()
        completion_deployment_id = str(
            getattr(control, "delivery_deployment_id", "")
            or getattr(control, "active_deployment_id", "")
            or configured.get("reasoningEngineV2DeploymentId")
            or ""
        )
        payload["marketObservationReasoningCompletion"] = (
            stores.reasoning_engine_job_store(configured).market_observation_completion_summary(
                completion_deployment_id,
                limit=12,
            )
        )
        return payload
    except Exception as error:  # noqa: BLE001 - diagnostics must remain readable during store recovery.
        return {"enabled": False, "queueHealth": {"status": "error", "reason": str(error)[:240]}}


def create_ontology_experiment_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().create(payload if isinstance(payload, dict) else {})


def suggest_ontology_experiments_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    symbols = body.get("symbols") if isinstance(body.get("symbols"), list) else []
    candidate_result = build_rule_change_candidate_service(runtime_settings()).propose(
        symbols=symbols,
        trigger=str(body.get("trigger") or "ontology-lab-suggest"),
        account_id=str(body.get("accountId") or body.get("account_id") or ""),
        tenant_id=str(body.get("tenantId") or body.get("tenant_id") or ""),
    )
    result = ontology_lab_service().suggest_from_rule_candidates(candidate_result, body)
    result["candidateResult"] = {
        "status": candidate_result.get("status"),
        "candidateCount": candidate_result.get("candidateCount"),
        "savedCount": candidate_result.get("savedCount"),
        "contextSummary": candidate_result.get("contextSummary") or {},
    }
    return result


def ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().report(experiment_id)


def run_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().run(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiment_payload(experiment_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendations(experiment_id, payload if isinstance(payload, dict) else {})


def apply_ontology_experiments_batch_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return ontology_lab_service().apply_recommendation_batch(payload if isinstance(payload, dict) else {})


def run_ontology_experiments_once_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    limit = int(body.get("limit") or 0)
    force = bool(body.get("force"))
    return ontology_lab_service().run_once(limit=limit, force=force)


def activate_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().activate(experiment_id)


def pause_ontology_experiment_payload(experiment_id: str) -> Dict[str, object]:
    return ontology_lab_service().pause(experiment_id)


def investment_strategy_proposal_service():
    return build_investment_strategy_proposal_service(runtime_settings())


def investment_strategy_proposal_read_service():
    # Read projections never publish lifecycle events. Avoid constructing the
    # durable event bus, whose schema checks belong to write/service startup.
    return build_investment_strategy_proposal_service(
        operational_read_settings(),
        event_publisher=EventBus(),
    )


def list_investment_strategy_proposals_payload(query: Dict[str, List[str]] = None) -> Dict[str, object]:
    query = query or {}
    return investment_strategy_proposal_read_service().list(
        limit=safe_int(first_query(query, "limit"), 100, 1, 500),
        detail="summary" if request_bool(first_query(query, "summary"), False) else "full",
    )


def investment_strategy_proposals_status_payload() -> Dict[str, object]:
    return investment_strategy_proposal_read_service().status()


def investment_strategy_proposal_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_read_service().get(proposal_id)


def validate_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().validate_materialization(proposal_id, payload if isinstance(payload, dict) else {})


def approve_investment_strategy_proposal_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().approve(proposal_id, payload if isinstance(payload, dict) else {})


def investment_strategy_proposal_performance_payload(proposal_id: str) -> Dict[str, object]:
    return investment_strategy_proposal_read_service().performance(proposal_id)


def record_investment_strategy_proposal_performance_payload(proposal_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_strategy_proposal_service().record_performance_sample(proposal_id, payload if isinstance(payload, dict) else {})


def notification_store():
    return stores.notification_template_store()


def notification_queue_store(settings: Dict[str, object] = None):
    read_settings = dict(settings or operational_read_settings())
    # The list API is a read model. Rule defaults and operational schema are
    # owned by service startup and write-side stores, not the first inbox read.
    read_settings["_skipNotificationRuleDefaultsSeed"] = "1"
    read_settings["_skipOperationalSchemaBootstrap"] = "1"
    return stores.notification_job_store(read_settings)


def notification_rule_store():
    return stores.notification_rule_store()


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


def compact_notification_text(value: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", full_notification_text(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def notification_action_label(action: object, target_role: object = "") -> str:
    code = str(action or "").strip().upper()
    watchlist = str(target_role or "").strip().lower() == "watchlist"
    labels = {
        "BUY": "소액 진입 검토",
        "ADD": "소액 추가매수 검토",
        "HOLD": "관심 유지" if watchlist else "보유 유지",
        "TRIM": "분할축소 검토",
        "SELL": "매도 검토",
        "AVOID": "신규 진입 회피",
    }
    return labels.get(code, code or "조건 확인")


def notification_action_flow(context: Dict[str, object]) -> Dict[str, object]:
    """Expose the user-facing TypeDB action flow without raw debug payloads."""

    context = context if isinstance(context, dict) else {}
    relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation:
        relation = context.get("relationContext") if isinstance(context.get("relationContext"), dict) else {}
    decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
    envelope = relation.get("actionEnvelope") if isinstance(relation.get("actionEnvelope"), dict) else {}
    if not envelope:
        envelope = decision.get("actionEnvelope") if isinstance(decision.get("actionEnvelope"), dict) else {}
    relation_diff = context.get("ontologyRelationDiff") if isinstance(context.get("ontologyRelationDiff"), dict) else {}
    transition = context.get("decisionTransition") if isinstance(context.get("decisionTransition"), dict) else {}
    if not transition:
        transition = relation_diff.get("decisionTransition") if isinstance(relation_diff.get("decisionTransition"), dict) else {}
    validated = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
    user_state = context.get("investmentNotificationState") if isinstance(context.get("investmentNotificationState"), dict) else {}
    user_transition = context.get("investmentNotificationTransition") if isinstance(context.get("investmentNotificationTransition"), dict) else {}
    target_role = str(envelope.get("targetRole") or decision.get("targetRole") or relation.get("targetRole") or "")
    action = str(validated.get("action") or envelope.get("preferredAction") or decision.get("candidateAction") or "")
    action_label = str(validated.get("actionLabel") or "") or notification_action_label(action, target_role)
    if not any([envelope, transition, action]):
        return {}

    effect_rows = envelope.get("effectLabels") if isinstance(envelope.get("effectLabels"), list) else []
    effects = []
    for item in effect_rows:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if label and label not in effects:
            effects.append(label)
    news_impact = context.get("newsImpact") if isinstance(context.get("newsImpact"), dict) else {}
    inline_news = {}
    if (
        news_impact.get("decisionChanging")
        and news_impact.get("decisionInlineEligible") is True
        and news_impact.get("decisionDriverConfirmed") is True
    ):
        inline_news = {
            "headline": compact_notification_text(user_friendly_ai_text(news_impact.get("headline") or "", 180), 180),
            "source": user_friendly_ai_text(news_impact.get("source") or "", 80),
            "impact": str(news_impact.get("impact") or "")[:80],
        }
    readiness = envelope.get("dataReadiness") if isinstance(envelope.get("dataReadiness"), dict) else {}
    transition_presentation = decision_transition_presentation(context, action)
    response = (
        NotificationAIValidatedResponse.from_dict(validated)
        if validated
        else NotificationAIValidatedResponse(action=action, action_label=action_label)
    )
    execution_plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    has_next_check = bool(response.next_checks or envelope.get("nextChecks") or execution_plan.get("nextChecks"))
    has_invalidation = bool(
        response.invalidation_condition
        or envelope.get("invalidationConditions")
        or execution_plan.get("weakenConditions")
    )
    next_action = compact_next_action_line(context, response) if has_next_check else ""
    invalidation = compact_invalidation_line(context, response) if has_invalidation else ""
    return {
        "status": str(envelope.get("status") or ""),
        "statusLabel": str(envelope.get("statusLabel") or ACTION_ENVELOPE_STATUS_LABELS.get(str(envelope.get("status") or "").upper(), "조건 확인")),
        "currentAction": action,
        "currentActionLabel": action_label,
        "userState": {
            "code": str(user_state.get("code") or ""),
            "label": str(user_state.get("label") or ""),
            "readiness": str(user_state.get("readiness") or ""),
            "readinessLabel": str(user_state.get("readinessLabel") or ""),
        } if user_state else {},
        "userTransition": {
            "kind": str(user_transition.get("kind") or ""),
            "changed": bool(user_transition.get("changed")),
            "changedFieldLabels": [str(item) for item in user_transition.get("changedFieldLabels") or []],
            "summary": compact_notification_text(str(user_transition.get("summary") or ""), 260),
            "previousState": dict(user_transition.get("previousState") or {}) if isinstance(user_transition.get("previousState"), dict) else {},
            "currentState": dict(user_transition.get("currentState") or {}) if isinstance(user_transition.get("currentState"), dict) else {},
        } if user_transition else {},
        "transition": {
            "kind": str(transition.get("kind") or ""),
            "category": str(transition_presentation.get("category") or ""),
            "label": str(transition_presentation.get("label") or ""),
            "summary": compact_notification_text(
                str(transition_presentation.get("summary") or user_friendly_ai_text(transition.get("summary") or "", 180)),
                220,
            ),
            "previousAction": str(transition.get("previousAction") or ""),
            "currentAction": str(transition.get("currentAction") or action),
            "previousStatus": str(transition.get("previousStatus") or ""),
            "currentStatus": str(transition.get("currentStatus") or envelope.get("status") or ""),
        },
        "effects": effects[:4],
        "nextChecks": [compact_notification_text(next_action, 180)] if next_action else [],
        "invalidationConditions": [compact_notification_text(invalidation, 180)] if invalidation else [],
        "dataReadiness": {
            "state": str(readiness.get("state") or ""),
            "dataState": str(readiness.get("dataState") or relation.get("dataState") or ""),
            "usable": bool(readiness.get("usable")) if readiness else None,
        },
        "newsImpact": inline_news,
    }


def full_notification_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def notification_customer_text(job: NotificationJob) -> str:
    """Render persisted AI decisions with the current customer-safe format."""

    context = job.context if isinstance(job.context, dict) else {}
    payload = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
    if payload:
        try:
            response = NotificationAIValidatedResponse.from_dict(payload)
            rendered = prepend_execution_start_badge(execution_telegram_message(context, response), context)
            if rendered:
                return rendered
        except Exception:  # noqa: BLE001 - an old incomplete payload must not hide a ledger item.
            pass
    return str(job.text or "")


def notification_processing_age_minutes(job: NotificationJob) -> float:
    started_at = parse_utc(str((job.context or {}).get("processingStartedAt") or job.updated_at or job.created_at or ""))
    if job.status != "processing" or not started_at:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds() / 60)


def notification_next_eligible_at(context: Dict[str, object]) -> str:
    if not context.get("cooldownEnabled"):
        return ""
    if str(context.get("cooldownDecision") or "") != "cooldown" and not context.get("cooldownSuppressed"):
        return ""
    last_sent_at = parse_utc(str(context.get("cooldownLastSentAt") or ""))
    if not last_sent_at:
        return ""
    try:
        minutes = int(float(context.get("cooldownMinutes") or 0))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return ""
    return utc_iso(last_sent_at + timedelta(minutes=minutes))


def notification_suppression_summary(job: NotificationJob) -> str:
    context = dict(job.context or {})
    if job.status != "suppressed":
        return ""
    if job.last_error:
        return job.last_error
    if context.get("cooldownReason"):
        return str(context.get("cooldownReason"))
    if context.get("marketHoursReason"):
        return str(context.get("marketHoursReason"))
    if context.get("quietHoursReason"):
        return str(context.get("quietHoursReason"))
    reason = str(context.get("deliverySuppressionReason") or "").strip()
    if reason in {"stale_data", "stale_data_at_dispatch"}:
        return "데이터 신선도 기준 미통과"
    if reason == "stale_data_recheck_requested":
        return "오래된 판단은 보내지 않고 최신 데이터 재수집을 예약했습니다."
    if reason == "market_closed":
        return "장 시간 외 발송 보류"
    if reason == "state_cooldown":
        return "같은 상태 반복 발송 보류"
    if reason == "initial_graph_baseline":
        return "최초 참고 관계는 비교 기준으로만 저장"
    if reason == "unchanged_graph_inference":
        return "TypeDB 행동 범위가 이전과 같아 반복 발송 보류"
    if reason == "unresolved_material_evidence":
        return "관계를 만든 정확한 원문 근거를 연결하지 못해 발송 보류"
    return reason or "알림 정책으로 발송 보류"


def notification_job_diagnostics(
    jobs: List[NotificationJob],
    *,
    stale_minutes: int = None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    if stale_minutes is None:
        configured_settings = settings or operational_read_settings()
        try:
            stale_minutes = max(1, int(configured_settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
    reason_counts: Dict[str, int] = {}
    stale_processing = 0
    for job in jobs:
        if job.status == "suppressed":
            reason = notification_suppression_summary(job) or "보류 사유 없음"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if job.status == "processing" and notification_processing_age_minutes(job) >= stale_minutes:
            stale_processing += 1
    top_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]
    return {
        "processingStaleMinutes": stale_minutes,
        "staleProcessingCount": stale_processing,
        "suppressionReasons": [{"reason": reason, "count": count} for reason, count in top_reasons],
    }


def notification_job_public_payload(
    job: NotificationJob,
    detail: bool = False,
    stale_minutes: int = None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    context = job.context or {}
    configured_settings = settings
    customer_text = notification_customer_text(job)
    reasons = context.get("deliveryReasons") if isinstance(context.get("deliveryReasons"), list) else []
    trigger_ledger = context.get("deliveryTriggerLedger") if isinstance(context.get("deliveryTriggerLedger"), list) else []
    delivery_explanation = (
        dict(context.get("customerDeliveryExplanation") or {})
        if isinstance(context.get("customerDeliveryExplanation"), dict)
        else {}
    )
    title_source = context.get("headline") if job.message_type == INVESTMENT_INSIGHT else (context.get("title") or context.get("headline") or "")
    if job.message_type == INVESTMENT_INSIGHT:
        validated = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
        if validated:
            try:
                title_source = execution_headline(context, NotificationAIValidatedResponse.from_dict(validated))
            except Exception:  # noqa: BLE001 - old incomplete alert payloads keep their saved title.
                pass
    title = notification_title_with_context_icon(
        job.message_type,
        title_source,
        context,
    )
    processing_age = notification_processing_age_minutes(job)
    episode = context.get("investmentDecisionEpisode") if isinstance(context.get("investmentDecisionEpisode"), dict) else {}
    relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    symbol = str(context.get("symbol") or context.get("rawSymbol") or "").strip().upper()
    decision_episode_id = str(
        context.get("investmentDecisionEpisodeId")
        or context.get("decisionEpisodeId")
        or episode.get("episodeId")
        or relation.get("investmentDecisionEpisodeId")
        or ""
    ).strip()
    decision_key = str(context.get("decisionKey") or "").strip()
    if not decision_key and symbol and decision_episode_id:
        decision_key = investment_decision_key(job.account_id or "default", symbol, decision_episode_id)
    data_quality = str(context.get("dataQuality") or relation.get("dataQuality") or "actual")
    data_mode = str(context.get("dataMode") or context.get("mode") or "").lower()
    is_mock = bool(context.get("isMock")) or data_quality.lower() in {"mock", "demo"} or data_mode in {"mock", "demo", "preview"}
    api_source = str(context.get("apiSource") or context.get("quoteSource") or context.get("sourceApi") or "notification_jobs")
    if stale_minutes is None:
        configured_settings = configured_settings or operational_read_settings()
        try:
            stale_minutes = max(1, int(configured_settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
    payload = {
        "jobId": job.job_id,
        "messageType": job.message_type,
        "messageTypeLabel": MESSAGE_TYPE_LABELS.get(job.message_type, job.message_type),
        "messageTypeIcon": notification_message_icon(job.message_type, context),
        "status": job.status,
        "accountId": job.account_id,
        "accountLabel": job.account_label,
        "decisionEpisodeId": decision_episode_id,
        "decisionKey": decision_key,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "sourceEventId": job.source_event_id,
        "sourceEventName": job.source_event_name,
        "title": title,
        "symbol": symbol,
        "rawSymbol": str(context.get("rawSymbol") or context.get("symbol") or "").strip(),
        "symbolName": str(context.get("symbolDisplayName") or context.get("displaySymbolName") or "").strip(),
        "textPreview": compact_notification_text(customer_text),
        "lastError": job.last_error,
        "suppressionSummary": notification_suppression_summary(job),
        "nextEligibleAt": notification_next_eligible_at(context),
        "processingAgeMinutes": round(processing_age, 1),
        "recoverableProcessing": bool(job.status == "processing" and processing_age >= stale_minutes),
        "deliveryDecision": context.get("deliveryDecision") or ("send" if job.status in {"pending", "processing", "done"} else job.status),
        "apiSource": api_source,
        "dataQuality": data_quality,
        "isMock": is_mock,
        "deliveryGateState": context.get("deliveryGateState") or "",
        "deliveryGateReason": context.get("deliveryGateReason") or "",
        "deliveryReasons": [str(item) for item in reasons],
        "deliveryTriggerLedgerVersion": context.get("deliveryTriggerLedgerVersion") or "",
        # This bounded, customer-safe contract drives the summary screen too.
        # Raw trigger provenance remains restricted to the detailed projection.
        "customerDeliveryExplanation": delivery_explanation,
        "customerDeliveryExplanationVersion": delivery_explanation.get("version") or "",
        "customerDeliveryExplanationValidationState": str((
            (delivery_explanation.get("validation") or {}).get("state")
            if isinstance(delivery_explanation.get("validation"), dict)
            else context.get("customerDeliveryExplanationValidationState")
        ) or ""),
        "deliveryTriggerLedger": [
            dict(item) for item in trigger_ledger if isinstance(item, dict)
        ] if detail else [],
        "customerDeliveryTriggers": [
            dict(item)
            for item in trigger_ledger
            if isinstance(item, dict) and item.get("customerVisible") is True
        ] if detail else [],
        "internalDeliveryChecks": [
            dict(item)
            for item in trigger_ledger
            if isinstance(item, dict) and item.get("customerVisible") is not True
        ] if detail else [],
        "deliveryFingerprint": context.get("deliveryFingerprint") or "",
        "deliveryReviewLevel": context.get("deliveryReviewLevel") or "",
        "deliveryDataState": context.get("deliveryDataState") or "",
        "deliveryChangeState": context.get("deliveryChangeState") or "",
        "deliveryConflictState": context.get("deliveryConflictState") or "",
        "deliveryValidationState": context.get("deliveryValidationState") or "",
        "repeatRecentCount": context.get("repeatRecentCount"),
        "repeatWindowMinutes": context.get("repeatWindowMinutes"),
        "repeatBypassed": bool(context.get("repeatBypassed")),
        "repeatBypassReason": context.get("repeatBypassReason") or "",
        "deliverySuppressionReason": context.get("deliverySuppressionReason") or "",
        "investmentNotificationState": dict(context.get("investmentNotificationState") or {}) if isinstance(context.get("investmentNotificationState"), dict) else {},
        "investmentNotificationTransition": dict(context.get("investmentNotificationTransition") or {}) if isinstance(context.get("investmentNotificationTransition"), dict) else {},
        "freshDataRecheck": dict(context.get("freshDataRecheck") or {}) if isinstance(context.get("freshDataRecheck"), dict) else {},
        "cooldownEnabled": bool(context.get("cooldownEnabled")),
        "cooldownMinutes": context.get("cooldownMinutes"),
        "cooldownRecentSentCount": context.get("cooldownRecentSentCount"),
        "cooldownLastSentAt": context.get("cooldownLastSentAt") or "",
        "cooldownLastSentAgeMinutes": context.get("cooldownLastSentAgeMinutes"),
        "cooldownDecision": context.get("cooldownDecision") or "",
        "cooldownReason": context.get("cooldownReason") or "",
        "cooldownSuppressed": bool(context.get("cooldownSuppressed")),
        "marketHoursEnabled": bool(context.get("marketHoursEnabled")),
        "marketHoursMarket": context.get("marketHoursMarket") or "",
        "marketHoursLabel": context.get("marketHoursLabel") or "",
        "marketHoursStatus": context.get("marketHoursStatus") or "",
        "marketHoursDecision": context.get("marketHoursDecision") or "",
        "marketHoursReason": context.get("marketHoursReason") or "",
        "marketHoursLocalTime": context.get("marketHoursLocalTime") or "",
        "marketHoursOpenTime": context.get("marketHoursOpenTime") or "",
        "marketHoursCloseTime": context.get("marketHoursCloseTime") or "",
        "marketHoursTimezone": context.get("marketHoursTimezone") or "",
        "offHoursDeliveryMode": context.get("offHoursDeliveryMode") or "",
        "quietHoursSuppressed": bool(context.get("quietHoursSuppressed")),
        "quietHoursReason": context.get("quietHoursReason") or "",
        "quietHoursStart": context.get("quietHoursStart") or "",
        "quietHoursEnd": context.get("quietHoursEnd") or "",
        "quietHoursTimezone": context.get("quietHoursTimezone") or "",
    }
    if detail:
        configured_settings = configured_settings or operational_read_settings()
        payload["fullText"] = full_notification_text(customer_text)
        payload["actionFlow"] = notification_action_flow(context)
        # The trace is rebuilt from the immutable context captured with this
        # job, never from the currently active graph generation.
        payload["reasoningTrace"] = build_notification_reverse_reasoning_trace(
            context,
            job_id=job.job_id,
            job_status=job.status,
        )
        try:
            relation = context.get("ontologyRelationContext")
            relation = dict(relation or {}) if isinstance(relation, dict) else {}
            generation_id = str(relation.get("inferenceGenerationId") or "").strip()
            if generation_id:
                execution_store = stores.ontology_projection_run_store(configured_settings)
                payload["reasoningTrace"]["executionLedger"] = (
                    execution_store.execution_trace_for_inference_generation(
                        generation_id,
                        account_id=job.account_id,
                    )
                )
        except Exception as error:  # noqa: BLE001 - saved notification trace remains available.
            payload["reasoningTrace"]["executionLedger"] = {
                "status": "error",
                "reason": str(error)[:220],
                "runCount": 0,
                "runs": [],
            }
        try:
            episode = context.get("investmentDecisionEpisode") if isinstance(context.get("investmentDecisionEpisode"), dict) else {}
            episode_id = str(
                context.get("investmentDecisionEpisodeId")
                or episode.get("episodeId")
                or ""
            ).strip()
            lifecycle = stores.investment_domain_store(configured_settings).lifecycle_trace(episode_id)
            payload["investmentLifecycle"] = lifecycle
            payload["reasoningTrace"]["investmentLifecycle"] = lifecycle
        except Exception as error:  # noqa: BLE001 - the immutable reasoning trace remains usable.
            payload["investmentLifecycle"] = {
                "status": "error",
                "reason": str(error)[:220],
            }
            payload["reasoningTrace"]["investmentLifecycle"] = payload["investmentLifecycle"]
    return payload


def notification_job_list_payload(job: NotificationJob, stale_minutes: int) -> Dict[str, object]:
    """Expose only the fields needed to render an outbox ledger row.

    The message body and policy audit trail remain available from the existing
    job-detail endpoint. This keeps a 20-row ledger from carrying 20 copies of
    full notification text and cooldown metadata.
    """
    payload = notification_job_public_payload(job, detail=False, stale_minutes=stale_minutes)
    if not payload.get("title"):
        headline = full_notification_text(job.text).split("\n", 1)[0].strip()
        payload["title"] = compact_notification_text(job.source_event_name or headline, 120)
    fields = {
        "jobId", "messageType", "messageTypeLabel", "messageTypeIcon", "status",
        "accountId", "accountLabel", "decisionEpisodeId", "decisionKey",
        "createdAt", "updatedAt", "sourceEventName", "title", "symbol", "rawSymbol",
        "symbolName", "textPreview", "lastError", "suppressionSummary", "nextEligibleAt",
        "processingAgeMinutes", "recoverableProcessing", "deliveryDecision",
        "apiSource", "dataQuality", "isMock",
    }
    return {key: value for key, value in payload.items() if key in fields}


def encode_notification_cursor(job: NotificationJob) -> str:
    raw = json.dumps({"updatedAt": job.updated_at or job.created_at, "jobId": job.job_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_notification_cursor(value: str) -> Dict[str, str]:
    cursor = str(value or "").strip()
    if not cursor:
        return {"updatedAt": "", "jobId": ""}
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return {
            "updatedAt": str(payload.get("updatedAt") or "")[:40],
            "jobId": str(payload.get("jobId") or "")[:191],
        }
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"updatedAt": "", "jobId": ""}


def notification_jobs_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 20)))
    offset = max(0, int(first_query(query, "offset") or 0))
    message_type = first_query(query, "messageType") or first_query(query, "message_type")
    status = first_query(query, "status")
    search = first_query(query, "query") or first_query(query, "q")
    scope = (first_query(query, "scope") or "investment").strip().lower()
    recipient_id = (first_query(query, "recipientId") or "local-owner").strip()[:191] or "local-owner"
    inbox = (first_query(query, "inbox") or "all").strip().lower()
    if inbox not in {"all", "unread", "important", "action"}:
        inbox = "all"
    cursor_value = first_query(query, "cursor") or ""
    cursor = decode_notification_cursor(cursor_value)
    if scope not in {"investment", "operations", "all"}:
        scope = "investment"
    try:
        settings = operational_read_settings()
        try:
            stale_minutes = max(1, int(settings.get("notificationProcessingStaleMinutes") or 2))
        except (TypeError, ValueError):
            stale_minutes = 2
        store = notification_queue_store(settings)
        if hasattr(store, "recent_list_page_with_summary"):
            jobs, total, summary = store.recent_list_page_with_summary(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
                recipient_id=recipient_id,
                inbox=inbox,
                cursor_updated_at=cursor["updatedAt"],
                cursor_job_id=cursor["jobId"],
            )
        elif hasattr(store, "recent_page_with_summary"):
            jobs, total, summary = store.recent_page_with_summary(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
            )
        else:
            jobs, total = store.recent_page(
                limit=limit,
                offset=offset,
                message_type=message_type,
                status=status,
                query=search,
                scope=scope,
            )
            summary = store.summary()
    except Exception:  # noqa: BLE001 - empty queue keeps the console readable without MySQL.
        jobs = []
        total = 0
        summary = {
            "pending": 0,
            "awaiting_ai": 0,
            "processing": 0,
            "done": 0,
            "superseded": 0,
            "suppressed": 0,
            "failed": 0,
        }
        stale_minutes = 30
        store = None
    receipts = {
        job.job_id: dict((job.context or {}).get("notificationReceipt") or {})
        for job in jobs
        if isinstance((job.context or {}).get("notificationReceipt"), dict)
    }
    inbox_summary = {"total": total, "unread": total, "important": 0, "actionRequired": 0}
    if store and hasattr(store, "receipt_states") and len(receipts) < len(jobs):
        receipts = store.receipt_states(recipient_id, [job.job_id for job in jobs])
    if store and hasattr(store, "inbox_summary"):
        inbox_summary = store.inbox_summary(recipient_id, scope=scope)
    items = []
    for job in jobs:
        item = notification_job_list_payload(job, stale_minutes=stale_minutes)
        receipt = receipts.get(job.job_id, {})
        item.update({
            "readAt": str(receipt.get("readAt") or ""),
            "acknowledgedAt": str(receipt.get("acknowledgedAt") or ""),
            "important": bool(receipt.get("important")),
            "receiptUpdatedAt": str(receipt.get("receiptUpdatedAt") or ""),
        })
        items.append(item)
    next_cursor = ""
    if jobs and len(jobs) >= limit and offset + len(jobs) < total:
        next_cursor = encode_notification_cursor(jobs[-1])
    return {
        "jobs": items,
        "summary": summary,
        "inboxSummary": inbox_summary,
        "diagnostics": notification_job_diagnostics(jobs, stale_minutes=stale_minutes),
        "limit": limit,
        "offset": offset,
        "total": total,
        "query": search,
        "messageType": message_type,
        "status": status,
        "scope": scope,
        "recipientId": recipient_id,
        "inbox": inbox,
        "cursor": cursor_value,
        "nextCursor": next_cursor,
    }


def _compact_notification_stage(stage: object) -> Dict[str, object]:
    value = dict(stage or {}) if isinstance(stage, dict) else {}
    return {
        key: value.get(key)
        for key in (
            "sequence", "key", "title", "status", "summary", "startedAt",
            "completedAt", "durationMs", "identifiers",
        )
        if value.get(key) not in (None, "", [], {})
    }


def _compact_notification_reasoning_trace(trace: object) -> Dict[str, object]:
    value = dict(trace or {}) if isinstance(trace, dict) else {}
    ai = dict(value.get("aiExecution") or {}) if isinstance(value.get("aiExecution"), dict) else {}
    narrative = dict(value.get("narrative") or {}) if isinstance(value.get("narrative"), dict) else {}
    rule_evaluations = [
        dict(item) for item in value.get("ruleEvaluations") or [] if isinstance(item, dict)
    ]
    proof_available = len([
        item for item in rule_evaluations
        if str(((item.get("proof") or {}).get("status") if isinstance(item.get("proof"), dict) else "") or "") == "available"
    ])
    return {
        key: value.get(key)
        for key in (
            "version", "status", "reason", "jobId", "jobStatus", "snapshotBound",
            "subject", "snapshot", "finalDecision", "aiComparison", "steps", "completeness",
            "missingData",
        )
        if value.get(key) not in (None, "", [], {})
    } | {
        "aiExecution": {
            key: ai.get(key)
            for key in (
                "status", "requestId", "model", "reasoningEffort", "reviewMode",
                "adoptionState", "actionAuthority", "validationState", "latencyMs",
                "executed", "responseSource", "writerProvenance", "claimPublication",
                "executionSpans",
            )
            if ai.get(key) not in (None, "", [], {})
        },
        "narrative": {
            "writerProvenance": narrative.get("writerProvenance") or {},
            "publication": narrative.get("publication") or {},
            "metrics": narrative.get("metrics") or {},
        },
        "ruleProofSummary": {
            "evaluationCount": len(rule_evaluations),
            "availableProofCount": proof_available,
            "legacyUnavailableCount": max(0, len(rule_evaluations) - proof_available),
        },
    }


def _compact_notification_pipeline(trace: object) -> Dict[str, object]:
    value = dict(trace or {}) if isinstance(trace, dict) else {}
    pipeline = dict(value.get("pipeline") or {}) if isinstance(value.get("pipeline"), dict) else {}
    compact_pipeline = {
        key: pipeline.get(key)
        for key in ("contractVersion", "status", "complete", "stageCount", "bottleneck", "links")
        if pipeline.get(key) not in (None, "", [], {})
    }
    compact_pipeline["stages"] = [_compact_notification_stage(item) for item in pipeline.get("stages") or []]
    return {
        "contractVersion": value.get("contractVersion") or "notification-trace-v2",
        "jobId": value.get("jobId") or "",
        "pipeline": compact_pipeline,
    }


def _notification_detail_section_payload(
    payload: Dict[str, object],
    section: str,
    *,
    include_sensitive: bool,
) -> Dict[str, object]:
    reasoning = dict(payload.get("reasoningTrace") or {})
    trace = dict(payload.get("notificationTrace") or {})
    if section == "reasoning":
        ai = dict(reasoning.get("aiExecution") or {})
        reasoning["aiExecution"] = {
            key: ai.get(key)
            for key in ("status", "requestId", "reviewMode", "adoptionState", "actionAuthority", "executed")
            if ai.get(key) not in (None, "", [], {})
        }
        reasoning.pop("narrative", None)
        return {"jobId": payload.get("jobId"), "section": section, "reasoning": reasoning}
    if section == "ai-review":
        ai = dict(reasoning.get("aiExecution") or {})
        if not include_sensitive:
            from ..application.notification.query import redact_notification_trace_data

            ai = dict(redact_notification_trace_data(ai) or {})
            ai.pop("prompt", None)
            ai["promptAccess"] = "owner-only"
        return {
            "jobId": payload.get("jobId"),
            "section": section,
            "aiExecution": ai,
            "aiRuntime": payload.get("aiRuntime") or {},
            "aiComparison": reasoning.get("aiComparison") or {},
            "finalDecision": reasoning.get("finalDecision") or {},
            "narrative": reasoning.get("narrative") or {},
        }
    if section == "delivery":
        pipeline = dict(trace.get("pipeline") or {})
        stages = [
            dict(item) for item in pipeline.get("stages") or []
            if isinstance(item, dict) and item.get("key") in {"source-event", "rendering", "delivery"}
        ]
        pipeline["stages"] = stages
        pipeline["stageCount"] = len(stages)
        return {
            "jobId": payload.get("jobId"),
            "section": section,
            "lifecycle": trace.get("lifecycle") or [],
            "deliveryAttempts": trace.get("deliveryAttempts") or [],
            "timeline": trace.get("timeline") or [],
            "pipeline": pipeline,
        }
    return {}


def notification_job_detail_payload(
    job_id: str,
    recipient_id: str = "local-owner",
    *,
    section: str = "summary",
    include_sensitive: bool = True,
) -> Dict[str, object]:
    configured = operational_read_settings()
    store = notification_queue_store(configured)
    job = store.get(job_id)
    if not job:
        return {}
    normalized_section = str(section or "summary").strip().lower()
    include_full_reasoning = normalized_section == "reasoning"
    payload = notification_job_public_payload(
        job,
        detail=include_full_reasoning,
        settings=configured,
    )
    if not include_full_reasoning:
        customer_text = notification_customer_text(job)
        payload["fullText"] = full_notification_text(customer_text)
        payload["actionFlow"] = notification_action_flow(job.context or {})
        payload["reasoningTrace"] = build_notification_reverse_reasoning_trace(
            job.context or {},
            job_id=job.job_id,
            job_status=job.status,
        )
    if hasattr(store, "receipt_states"):
        receipt = store.receipt_states(recipient_id, [job.job_id]).get(job.job_id, {})
        payload.update({
            "readAt": str(receipt.get("readAt") or ""),
            "acknowledgedAt": str(receipt.get("acknowledgedAt") or ""),
            "important": bool(receipt.get("important")),
            "receiptUpdatedAt": str(receipt.get("receiptUpdatedAt") or ""),
        })
    try:
        from ..application.notification.query import NotificationTraceQueryService

        source_event = {}
        if job.source_event_id and normalized_section in {"reasoning", "delivery"}:
            try:
                event = stores.event_log(configured).get(job.source_event_id)
                source_event = event.to_dict() if event else {}
            except Exception as error:  # noqa: BLE001 - a pruned event must not hide the remaining lineage.
                source_event = {"event_id": job.source_event_id, "lookupError": str(error)}
        context = dict(job.context or {})
        case_context = context.get("investmentReasoningCase")
        case_context = dict(case_context or {}) if isinstance(case_context, dict) else {}
        case_id = str(
            context.get("investmentReasoningCaseId")
            or case_context.get("caseId")
            or ""
        ).strip()
        reasoning_case = {}
        subject_case_context = context.get("investmentSubjectDecisionCase")
        subject_case_context = dict(subject_case_context or {}) if isinstance(subject_case_context, dict) else {}
        subject_case_id = str(
            context.get("investmentSubjectDecisionCaseId")
            or subject_case_context.get("subjectCaseId")
            or ""
        ).strip()
        subject_case = {}
        if case_id and normalized_section == "reasoning":
            try:
                case = stores.investment_reasoning_case_store(configured).get(case_id)
                reasoning_case = case.to_dict() if case else case_context
            except Exception as error:  # noqa: BLE001 - compact immutable context remains usable.
                reasoning_case = {**case_context, "caseId": case_id, "lookupError": str(error)}
        if subject_case_id and normalized_section == "reasoning":
            try:
                item = stores.subject_decision_case_store(configured).get(subject_case_id)
                subject_case = item.to_dict() if item else subject_case_context
            except Exception as error:  # noqa: BLE001 - compact immutable context remains usable.
                subject_case = {
                    **subject_case_context,
                    "subjectCaseId": subject_case_id,
                    "lookupError": str(error),
                }
        ai_trace = {}
        if normalized_section == "ai-review":
            try:
                ai_trace = stores.ai_inference_queue_store(configured).trace_for_notification(job.job_id)
            except Exception as error:  # noqa: BLE001 - notification execution audit is the fallback.
                ai_trace = {"lookupError": str(error)}
        if normalized_section in {"summary", "delivery"}:
            payload["notificationTrace"] = NotificationTraceQueryService(store).trace_for_job(
                job,
                reasoning_trace=payload.get("reasoningTrace") or {},
                source_event=source_event,
                reasoning_case=reasoning_case,
                ai_trace=ai_trace,
                rendered_message=str(payload.get("fullText") or ""),
                include_stage_details=False,
            )
        if ai_trace:
            payload["aiRuntime"] = ai_trace
        if reasoning_case and isinstance(payload.get("reasoningTrace"), dict):
            payload["reasoningTrace"]["reasoningCase"] = reasoning_case
        if subject_case and isinstance(payload.get("reasoningTrace"), dict):
            payload["reasoningTrace"]["subjectDecisionCase"] = subject_case
    except Exception as error:  # noqa: BLE001 - the saved notification remains readable without its timeline.
        payload["notificationTrace"] = {
            "contractVersion": "notification-trace-v2",
            "jobId": job.job_id,
            "status": "error",
            "reason": str(error)[:220],
            "lifecycle": [],
            "deliveryAttempts": [],
            "timeline": [],
            "pipeline": {
                "contractVersion": "notification-pipeline-trace-v1",
                "status": "error",
                "complete": False,
                "stageCount": 0,
                "stages": [],
            },
        }
    if normalized_section in {"reasoning", "ai-review", "delivery"}:
        return _notification_detail_section_payload(
            payload,
            normalized_section,
            include_sensitive=include_sensitive,
        )
    payload["detailContractVersion"] = "notification-detail-v2"
    payload["detailSections"] = {
        "summary": "/api/notification-jobs/" + job.job_id,
        "reasoning": "/api/notification-jobs/" + job.job_id + "/reasoning",
        "aiReview": "/api/notification-jobs/" + job.job_id + "/ai-review",
        "delivery": "/api/notification-jobs/" + job.job_id + "/delivery",
    }
    payload["reasoningTrace"] = _compact_notification_reasoning_trace(payload.get("reasoningTrace"))
    payload["notificationTrace"] = _compact_notification_pipeline(payload.get("notificationTrace"))
    return {"job": payload}


def update_notification_receipt_payload(job_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    store = notification_queue_store()
    if not store.get(job_id):
        return {"error": "알림을 찾지 못했습니다."}
    receipt = store.update_receipt(
        job_id,
        str(body.get("recipientId") or "local-owner"),
        read=request_bool(body.get("read")) if "read" in body else None,
        acknowledged=request_bool(body.get("acknowledged")) if "acknowledged" in body else None,
        important=request_bool(body.get("important")) if "important" in body else None,
    )
    return {"receipt": receipt, "inboxSummary": store.inbox_summary(receipt["recipientId"], scope="investment")}


def mark_all_notifications_read_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    recipient_id = str(body.get("recipientId") or "local-owner")
    scope = str(body.get("scope") or "investment")
    store = notification_queue_store()
    updated = store.mark_all_read(recipient_id, scope=scope)
    return {"updated": updated, "inboxSummary": store.inbox_summary(recipient_id, scope=scope)}


def replay_notification_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    identifier = configured(body.get("identifier") or body.get("notificationNumber") or body.get("jobId"))
    result = NotificationReplayService(
        queue=notification_queue_store(),
        account_repository=stores.account_registry(),
        runner_factory=build_notification_queue_runner,
        lookup_limit=int(body.get("lookupLimit") or 200),
    ).replay(
        identifier,
        direct=request_bool(body.get("direct")),
        dry_run=request_bool(body.get("dryRun", body.get("dry_run"))),
    )
    return result.to_dict()


def _research_evidence_summary_source_payload() -> Dict[str, object]:
    store = stores.research_evidence_store()
    analysis_rows = list(store.latest(kind="news", limit=500) or []) if hasattr(store, "latest") else []
    official_rows = []
    if hasattr(store, "latest"):
        for official_kind in ["disclosure", "filing", "sec-filing"]:
            official_rows.extend(list(store.latest(kind=official_kind, limit=500) or []))
    return {
        "status": "ok",
        "summary": store.summary(),
        "articleAnalysis": research_evidence_article_analysis_summary(analysis_rows),
        "officialAnalysis": research_evidence_official_analysis_summary(official_rows),
    }


def research_evidence_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 8)))
    offset = max(0, int(first_query(query, "offset") or 0))
    symbol = configured(first_query(query, "symbol")).upper()
    kind = configured(first_query(query, "kind"))
    search = configured(first_query(query, "query") or first_query(query, "q"))
    page_key = "|".join([symbol or "all", kind or "all", search or "all", str(limit), str(offset)])

    def load_page() -> Dict[str, object]:
        store = stores.research_evidence_store()
        items, total = store.latest_page(
            symbol=symbol,
            kind=kind,
            limit=limit,
            offset=offset,
            query=search,
        )
        return {
            "items": [research_evidence_list_payload(item) for item in items],
            "claimQuality": claim_quality_summary(items),
            "total": total,
        }

    page = cached_api_payload(
        RESEARCH_EVIDENCE_PAGE_READ_MODEL,
        page_key,
        load_page,
        force=request_bool(first_query(query, "refresh"), False),
    )
    items = list(page.get("items") or [])
    total = int(page.get("total") or 0)
    summaries = cached_api_payload(
        RESEARCH_EVIDENCE_SUMMARY_READ_MODEL,
        "all",
        _research_evidence_summary_source_payload,
        force=request_bool(first_query(query, "refreshSummary"), False),
    )
    return {
        "items": items,
        "summary": summaries.get("summary") or {"total": total},
        "claimQuality": page.get("claimQuality") or {},
        "articleAnalysis": summaries.get("articleAnalysis") or {},
        "officialAnalysis": summaries.get("officialAnalysis") or {},
        "readCache": {
            "page": page.get("readCache") or {},
            "summary": summaries.get("readCache") or {},
        },
        "symbol": symbol,
        "kind": kind,
        "limit": limit,
        "offset": offset,
        "total": total,
        "query": search,
    }


def research_evidence_official_analysis_summary(items) -> Dict[str, object]:
    rows = [
        item for item in items or []
        if str(getattr(item, "kind", "") or "").lower() in {"disclosure", "filing", "sec-filing"}
    ]
    counts = {
        "officialCount": len(rows),
        "metadataOnlyCount": 0,
        "documentVerifiedCount": 0,
        "analysisReadyCount": 0,
        "alertEligibleCount": 0,
        "promptEligibleCount": 0,
        "needsReviewCount": 0,
        "providerCounts": {},
        "issueCounts": {},
    }
    for item in rows:
        raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
        quality = raw.get("disclosureDocumentQuality") if isinstance(raw.get("disclosureDocumentQuality"), dict) else {}
        analysis = raw.get("disclosureAnalysis") if isinstance(raw.get("disclosureAnalysis"), dict) else {}
        admission = assess_prompt_evidence(
            raw,
            kind=item.kind,
            published_at=item.published_at,
            observed_at=item.observed_at,
        ).to_dict()
        counts["metadataOnlyCount"] += int(str(raw.get("officialDocumentState") or "") == "metadata-only")
        counts["documentVerifiedCount"] += int(bool(raw.get("documentVerified")))
        counts["analysisReadyCount"] += int(bool(raw.get("analysisReady")))
        counts["alertEligibleCount"] += int(bool(admission.get("alertEligible")))
        counts["promptEligibleCount"] += int(bool(admission.get("promptEligible")))
        counts["needsReviewCount"] += int(bool(analysis.get("needsReview")))
        provider = str(item.source or "unknown")
        counts["providerCounts"][provider] = int(counts["providerCounts"].get(provider) or 0) + 1
        for issue in quality.get("issues") or []:
            key = str(issue or "unknown")
            counts["issueCounts"][key] = int(counts["issueCounts"].get(key) or 0) + 1
    counts["providerCounts"] = dict(sorted(counts["providerCounts"].items()))
    counts["issueCounts"] = dict(sorted(counts["issueCounts"].items()))
    return counts


def research_evidence_article_analysis_summary(items) -> Dict[str, object]:
    """Expose a bounded, display-only quality funnel for retained news."""
    rows = [item for item in items or [] if str(getattr(item, "kind", "")) == "news"]
    counts = {
        "newsCount": len(rows),
        "bodyReadCount": 0,
        "translationCompleteCount": 0,
        "translationPendingCount": 0,
        "translationUnavailableCount": 0,
        "summaryReadyCount": 0,
        "summaryNeedsReviewCount": 0,
        "summaryBlockedCount": 0,
        "analysisFallbackCount": 0,
        "analysisDeferredCount": 0,
        "displayEligibleCount": 0,
        "alertEligibleCount": 0,
        "reasoningEligibleCount": 0,
        "promptEligibleCount": 0,
        "decisionEligibleCount": 0,
        "stalePromptBlockedCount": 0,
        "provenanceCompleteCount": 0,
        "unresolvedPublisherCount": 0,
        "duplicatePublicationCount": 0,
        "independentConfirmationCount": 0,
        "sameStoryCount": 0,
        "followUpCount": 0,
        "contentInvalidReviewCount": 0,
        "bodyQualityBlockedCount": 0,
        "eventClusterCount": 0,
        "publisherTierCounts": {},
        "contentTypeCounts": {},
        "distributionChannelCounts": {},
    }
    event_clusters = set()
    for item in rows:
        eligibility = evidence_eligibility(item)
        counts["displayEligibleCount"] += int(bool(eligibility.get("displayEligible")))
        counts["alertEligibleCount"] += int(bool(eligibility.get("alertEligible")))
        counts["reasoningEligibleCount"] += int(bool(eligibility.get("reasoningEligible")))
        raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
        prompt_admission = assess_prompt_evidence(
            raw,
            kind=getattr(item, "kind", "news"),
            published_at=getattr(item, "published_at", ""),
            observed_at=getattr(item, "observed_at", ""),
        ).to_dict()
        counts["promptEligibleCount"] += int(bool(prompt_admission.get("promptEligible")))
        counts["decisionEligibleCount"] += int(bool(prompt_admission.get("decisionEligible")))
        counts["stalePromptBlockedCount"] += int(
            "evidence-stale" in list(prompt_admission.get("reasonCodes") or [])
        )
        source_identity = eligibility.get("sourceIdentity") if isinstance(eligibility.get("sourceIdentity"), dict) else {}
        provenance = raw.get("sourceProvenance") if isinstance(raw.get("sourceProvenance"), dict) else {}
        original = provenance.get("originalPublisher") if isinstance(provenance.get("originalPublisher"), dict) else {}
        counts["provenanceCompleteCount"] += int(bool(provenance.get("provenanceComplete")))
        counts["unresolvedPublisherCount"] += int(not source_identity.get("publisherId") or source_identity.get("publisherId") == "unknown")
        relationship = str(provenance.get("evidenceRelationship") or raw.get("evidenceRelationship") or "")
        counts["duplicatePublicationCount"] += int(relationship in {"exact-duplicate", "syndicated-copy"})
        counts["independentConfirmationCount"] += int(relationship == "independent-confirmation")
        counts["sameStoryCount"] += int(relationship == "same-story")
        counts["followUpCount"] += int(relationship == "follow-up")
        cluster_id = str(raw.get("storyClusterId") or "")
        if cluster_id:
            event_clusters.add(cluster_id)
        counts["contentInvalidReviewCount"] += int(str(eligibility.get("reviewState") or "") == "content-invalid")
        tier = str(source_identity.get("publisherTier") or original.get("tier") or "D")
        content_type = str(source_identity.get("contentType") or provenance.get("contentType") or "unknown")
        channel = str(source_identity.get("distributionChannel") or provenance.get("distributionChannel") or "direct")
        counts["publisherTierCounts"][tier] = int(counts["publisherTierCounts"].get(tier) or 0) + 1
        counts["contentTypeCounts"][content_type] = int(counts["contentTypeCounts"].get(content_type) or 0) + 1
        counts["distributionChannelCounts"][channel] = int(counts["distributionChannelCounts"].get(channel) or 0) + 1
        facts = raw.get("articleFacts") if isinstance(raw.get("articleFacts"), dict) else {}
        counts["bodyQualityBlockedCount"] += int(facts.get("bodyQualityPassed") is False or raw.get("bodyQualityPassed") is False)
        if str(raw.get("articleReadStatus") or facts.get("readStatus") or "") == "body" or bool(facts.get("bodyAvailable")):
            counts["bodyReadCount"] += 1
        language = str(raw.get("sourceLanguage") or "").strip().lower()
        translation_status = str(raw.get("translationStatus") or "").strip().lower()
        if language == "en":
            if translation_status == "complete":
                counts["translationCompleteCount"] += 1
            elif translation_status == "unavailable":
                counts["translationUnavailableCount"] += 1
            else:
                counts["translationPendingCount"] += 1
        quality = raw.get("articleSummaryQuality") if isinstance(raw.get("articleSummaryQuality"), dict) else {}
        quality_state = str(quality.get("state") or raw.get("summaryQualityState") or "").strip().lower()
        if quality_state == "ready":
            counts["summaryReadyCount"] += 1
        elif quality_state == "blocked":
            counts["summaryBlockedCount"] += 1
        else:
            counts["summaryNeedsReviewCount"] += 1
        analysis = raw.get("aiAnalysis") if isinstance(raw.get("aiAnalysis"), dict) else {}
        status = str(analysis.get("status") or "").strip().lower()
        if status == "fallback":
            counts["analysisFallbackCount"] += 1
        elif status == "deferred":
            counts["analysisDeferredCount"] += 1
    counts["eventClusterCount"] = len(event_clusters)
    return counts


def revalidate_research_evidence_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    result = ResearchEvidenceGovernanceService(
        stores.research_evidence_store(),
        runtime_settings(),
    ).revalidate(
        symbol=configured(body.get("symbol")).upper(),
        limit=max(1, min(5000, int(body.get("limit") or 500))),
    )
    new_domain_event(
        APP_ITEM_UPDATED,
        "research-evidence-governance",
        {"type": "researchEvidenceGovernance", "result": result},
    )
    return result


def research_evidence_list_payload(item, include_detail: bool = False) -> Dict[str, object]:
    item, analysis_source = projected_research_evidence(item)
    news_eligibility = evidence_eligibility(item) if str(item.kind or "").lower() == "news" else {}
    raw = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    states = item.state_payload()
    governance = raw.get("evidenceGovernance") if isinstance(raw.get("evidenceGovernance"), dict) else {}
    prompt_admission = assess_prompt_evidence(
        raw,
        kind=item.kind,
        published_at=item.published_at,
        observed_at=item.observed_at,
    ).to_dict()
    source_identity = news_eligibility.get("sourceIdentity") if isinstance(news_eligibility.get("sourceIdentity"), dict) else {}
    source_provenance = raw.get("sourceProvenance") if isinstance(raw.get("sourceProvenance"), dict) else {}
    original_publisher = source_provenance.get("originalPublisher") if isinstance(source_provenance.get("originalPublisher"), dict) else {}
    article_verification = source_provenance.get("articleVerification") if isinstance(source_provenance.get("articleVerification"), dict) else {}
    claim_ledger = raw.get("claimLedger") if isinstance(raw.get("claimLedger"), dict) else {}
    claim_summary = claim_ledger.get("summary") if isinstance(claim_ledger.get("summary"), dict) else {}
    disclosure_analysis = raw.get("disclosureAnalysis") if isinstance(raw.get("disclosureAnalysis"), dict) else {}
    disclosure_quality = raw.get("disclosureDocumentQuality") if isinstance(raw.get("disclosureDocumentQuality"), dict) else {}
    document_lifecycle = raw.get("documentLifecycle") if isinstance(raw.get("documentLifecycle"), dict) else {}
    article_summary_ko = str(raw.get("articleSummaryKo") or "")
    article_summary_quality = raw.get("articleSummaryQuality") if isinstance(raw.get("articleSummaryQuality"), dict) else {}
    summary_issues = list(article_summary_quality.get("issues") or [])
    if has_mojibake(article_summary_ko) or "text-encoding-corrupt" in summary_issues:
        # Never pass unreadable legacy source text to compact feed cards. The
        # raw article remains auditable in storage and the async worker can
        # retry it when a clean source becomes available.
        article_summary_ko = "원문 인코딩 점검으로 요약을 보류했습니다."
    compact_raw = {}
    for key in ["name", "provider", "articleType", "analysisStatus", "relevanceState", "impactLabel", "impactSummary", "koreanSummary", "priceImpact", "sourceTrustState", "materialityState", "dataState", "validationState", "articleReadStatus", "stockImpact", "stockImpactLabel", "stockImpactPolarity", "stockImpactReasonKo", "originalTitle", "translatedTitleKo", "sourceLanguage", "translationStatus", "summaryQualityState", "articleSummaryQuality", "externalFactDatasetId", "externalFactSourceRevision", "officialDocumentDatasetId", "officialDocumentFactRevision", "officialDocumentFactPayloadHash", "officialDocumentFetchedAt"]:
        if raw.get(key) not in (None, "", [], {}):
            compact_raw[key] = raw.get(key)
    payload = {
        "evidenceId": item.evidence_id,
        "symbol": item.symbol,
        "kind": item.kind,
        "source": item.source,
        "title": item.title,
        "summary": item.summary,
        "url": item.url,
        "observedAt": item.observed_at,
        "publishedAt": item.published_at,
        "polarity": item.polarity,
        "evidenceRole": item.polarity,
        "relationScope": str(raw.get("relationScope") or ""),
        "eventType": str(raw.get("eventType") or ""),
        "sourceTrustState": states["sourceTrustState"],
        "materialityState": states["materialityState"],
        "dataState": states["dataState"],
        "validationState": states["validationState"],
        "analysisSummary": str(raw.get("analysisSummary") or ""),
        "articleSummaryKo": article_summary_ko,
        "originalTitle": str(raw.get("originalTitle") or item.title or ""),
        "translatedTitleKo": str(raw.get("translatedTitleKo") or ""),
        "sourceLanguage": str(raw.get("sourceLanguage") or ""),
        "translationStatus": str(raw.get("translationStatus") or ""),
        "summaryQualityState": str(raw.get("summaryQualityState") or ""),
        "articleSummaryQuality": article_summary_quality,
        "analysisStatus": str((raw.get("aiAnalysis") or {}).get("status") or raw.get("analysisStatus") or "") if isinstance(raw.get("aiAnalysis") or {}, dict) else str(raw.get("analysisStatus") or ""),
        "articleReadStatus": str(raw.get("articleReadStatus") or ""),
        "stockImpact": str(raw.get("stockImpact") or ""),
        "stockImpactLabel": str(raw.get("stockImpactLabel") or ""),
        "stockImpactPolarity": str(raw.get("stockImpactPolarity") or ""),
        "stockImpactReasonKo": str(raw.get("stockImpactReasonKo") or ""),
        "sourceKind": str(raw.get("sourceKind") or ""),
        "sourcePlatform": str(raw.get("sourcePlatform") or ""),
        "newsEligibility": news_eligibility,
        "archiveEligible": bool(news_eligibility.get("archiveEligible")) if news_eligibility else True,
        "displayEligible": bool(news_eligibility.get("displayEligible")) if news_eligibility else bool(prompt_admission.get("displayEligible")),
        "alertEligible": bool(news_eligibility.get("alertEligible")) if news_eligibility else bool(prompt_admission.get("alertEligible")),
        "reasoningEligible": bool(news_eligibility.get("reasoningEligible")) if news_eligibility else bool(prompt_admission.get("decisionEligible")),
        "promptEvidenceAdmission": prompt_admission,
        "eligibilityAudit": {
            "displayEligible": bool(news_eligibility.get("displayEligible")) if news_eligibility else bool(prompt_admission.get("displayEligible")),
            "alertEligible": bool(news_eligibility.get("alertEligible")) if news_eligibility else bool(prompt_admission.get("alertEligible")),
            "reasoningEligible": bool(news_eligibility.get("reasoningEligible")) if news_eligibility else bool(prompt_admission.get("decisionEligible")),
            "promptEligible": bool(prompt_admission.get("promptEligible")),
            "usage": str(prompt_admission.get("usage") or "blocked"),
            "freshnessState": str(prompt_admission.get("freshnessState") or "unknown"),
            "reasonCodes": list(prompt_admission.get("reasonCodes") or []),
            "reviewReasonCodes": list(news_eligibility.get("reviewReasonCodes") or []) if news_eligibility else [],
        },
        "reviewState": str(news_eligibility.get("reviewState") or "") if news_eligibility else "",
        "reviewReasonCodes": list(news_eligibility.get("reviewReasonCodes") or []) if news_eligibility else [],
        "storyClusterId": str(raw.get("storyClusterId") or ""),
        "storyRootEvidenceId": str(source_provenance.get("storyRootEvidenceId") or raw.get("storyRootEvidenceId") or ""),
        "officialDocumentState": str(raw.get("officialDocumentState") or ""),
        "documentVerified": bool(raw.get("documentVerified")),
        "analysisReady": bool(raw.get("analysisReady")),
        "metadataVerified": bool(raw.get("metadataVerified")),
        "documentHash": str(raw.get("documentHash") or ""),
        "documentCharCount": int(raw.get("documentCharCount") or disclosure_quality.get("documentCharCount") or 0),
        "externalFactDatasetId": str(raw.get("externalFactDatasetId") or ""),
        "externalFactSourceRevision": str(raw.get("externalFactSourceRevision") or ""),
        "officialDocumentDatasetId": str(raw.get("officialDocumentDatasetId") or ""),
        "officialDocumentFactRevision": str(raw.get("officialDocumentFactRevision") or ""),
        "officialDocumentFactPayloadHash": str(raw.get("officialDocumentFactPayloadHash") or ""),
        "officialDocumentFetchedAt": str(raw.get("officialDocumentFetchedAt") or ""),
        "officialDocumentPreview": str(raw.get("officialDocumentPreview") or "")[:2000],
        "disclosureDocumentQuality": disclosure_quality,
        "documentLifecycle": document_lifecycle,
        "disclosureAnalysis": {
            "status": str(disclosure_analysis.get("status") or ""),
            "version": str(disclosure_analysis.get("version") or ""),
            "source": str(disclosure_analysis.get("source") or ""),
            "summary": str(disclosure_analysis.get("summary") or ""),
            "impactSummary": str(disclosure_analysis.get("impactSummary") or ""),
            "uncertaintySummary": str(disclosure_analysis.get("uncertaintySummary") or ""),
            "confirmedFacts": list(disclosure_analysis.get("confirmedFacts") or [])[:4],
            "materialNumbers": list(disclosure_analysis.get("materialNumbers") or [])[:12],
            "documentDates": list(disclosure_analysis.get("documentDates") or [])[:8],
            "watchItems": list(disclosure_analysis.get("watchItems") or [])[:4],
            "sourceSections": list(disclosure_analysis.get("sourceSections") or [])[:4],
            "needsReview": bool(disclosure_analysis.get("needsReview")),
            "lines": list(disclosure_analysis.get("lines") or [])[:6],
        },
        "sourceRevision": str(raw.get("sourceRevision") or raw.get("receiptNo") or raw.get("accessionNumber") or ""),
        "sourceAsOf": str(raw.get("sourceAsOf") or item.published_at or item.observed_at or ""),
        "sourceFetchedAt": str(raw.get("sourceFetchedAt") or ""),
        "sourceDocuments": {
            "primaryUrl": str(item.url or raw.get("officialDocumentUrl") or ""),
            "filingIndexUrl": str(raw.get("filingIndexUrl") or ""),
            "primaryDocument": str(raw.get("primaryDocument") or ""),
            "receiptNo": str(raw.get("receiptNo") or ""),
            "accessionNumber": str(raw.get("accessionNumber") or ""),
        },
        "disclosureCategory": str(raw.get("disclosureCategory") or ""),
        "disclosureTaxonomyVersion": str(raw.get("version") or "") if str(item.kind or "").lower() in {"disclosure", "filing", "sec-filing"} else "",
        "publisher": str(source_identity.get("publisher") or original_publisher.get("name") or raw.get("articlePublisher") or item.source),
        "publisherId": str(source_identity.get("publisherId") or original_publisher.get("publisherId") or raw.get("sourceOrigin") or ""),
        "publisherDomain": str(source_identity.get("publisherDomain") or original_publisher.get("domain") or ""),
        "publisherTier": str(source_identity.get("publisherTier") or original_publisher.get("tier") or ""),
        "publisherType": str(source_identity.get("publisherType") or original_publisher.get("publisherType") or ""),
        "declaredPublisher": str(source_identity.get("declaredPublisher") or source_provenance.get("declaredPublisher") or ""),
        "republisher": str(source_identity.get("republisher") or source_provenance.get("republisher") or ""),
        "distributionChannel": str(source_identity.get("distributionChannel") or source_provenance.get("distributionChannel") or raw.get("provider") or ""),
        "contentType": str(source_identity.get("contentType") or source_provenance.get("contentType") or raw.get("contentType") or ""),
        "syndicationState": str(source_provenance.get("syndicationState") or raw.get("syndicationState") or ""),
        "evidenceRelationship": str(source_provenance.get("evidenceRelationship") or raw.get("evidenceRelationship") or ""),
        "provenanceComplete": bool(source_provenance.get("provenanceComplete")),
        "sourcePath": list(source_provenance.get("sourcePath") or []),
        "articleVerification": article_verification,
        "sourceProvenance": source_provenance,
        "claimVerification": {
            "claimState": str(governance.get("claimState") or ""),
            "verificationStatus": str(governance.get("verificationStatus") or ""),
            "investmentJudgmentEligible": bool(governance.get("investmentJudgmentEligible")),
            "sourcePublisher": str(governance.get("sourcePublisher") or raw.get("sourcePublisher") or item.source),
            "sourceOrigin": str(governance.get("sourceOrigin") or raw.get("sourceOrigin") or ""),
            "independentSourceCount": int(governance.get("independentSourceCount") or 0),
            "officialEvidenceIds": list(governance.get("officialEvidenceIds") or []),
            "corroboratingEvidenceIds": list(governance.get("corroboratingEvidenceIds") or []),
            "conflictingEvidenceIds": list(governance.get("conflictingEvidenceIds") or []),
            "supersededByEvidenceId": str(governance.get("supersededByEvidenceId") or ""),
            "claimCount": int(claim_summary.get("claimCount") or 0),
            "eligibleClaimCount": int(claim_summary.get("eligibleClaimCount") or 0),
        },
        "analysisSource": analysis_source,
        "payload": compact_raw,
        "detailPath": "/api/research-evidence/" + urllib.parse.quote(str(item.evidence_id or "")),
    }
    if not include_detail:
        for key in [
            "newsEligibility", "eligibilityAudit", "reviewReasonCodes",
            "officialDocumentPreview", "disclosureDocumentQuality", "documentLifecycle",
            "sourceDocuments", "sourcePath", "articleVerification",
            "sourceProvenance",
        ]:
            payload.pop(key, None)
    return payload


def projected_research_evidence(item):
    """Fill legacy news analysis fields without making a network call.

    Historical evidence predates the article-analysis contract.  The list and
    detail APIs project those rows through the same deterministic analyser used
    by collection, so an empty field never falls back to title-keyword UI
    classification.  The worker persists this projection for new rows; this
    adapter keeps old rows truthful until their normal retention cycle ends.
    """
    raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
    has_legacy_analysis = (
        bool(raw.get("articleSummaryKo"))
        and bool(raw.get("stockImpactPolarity"))
        and not has_mojibake(raw.get("articleSummaryKo"))
    )
    if getattr(item, "kind", "") != "news" or news_ai_analysis_is_current(item) or has_legacy_analysis:
        return item, "stored"
    target = NewsCollectionTarget(
        symbol=str(getattr(item, "symbol", "") or ""),
        name=str(raw.get("name") or getattr(item, "symbol", "") or ""),
        market=str(raw.get("market") or ""),
        currency=str(raw.get("currency") or ""),
        sector=str(raw.get("sector") or ""),
    )
    try:
        analysis = local_news_ai_analysis(target, item).to_dict()
        return apply_news_ai_analysis(item, analysis), "legacy-projection"
    except Exception:  # noqa: BLE001 - an incomplete legacy article must remain readable.
        return item, "unavailable"


def research_evidence_detail_payload(evidence_id: str) -> Dict[str, object]:
    item = stores.research_evidence_store().get(evidence_id)
    if not item:
        return {}
    projected, analysis_source = projected_research_evidence(item)
    payload = projected.to_dict()
    payload.update(research_evidence_list_payload(projected, include_detail=True))
    payload["payload"] = dict(projected.raw_payload or {})
    payload["promptEvidenceAdmission"] = assess_prompt_evidence(
        projected.raw_payload,
        kind=projected.kind,
        published_at=projected.published_at,
        observed_at=projected.observed_at,
    ).to_dict()
    payload["analysisSource"] = analysis_source
    return {"item": payload}


def delete_research_evidence_payload(evidence_id: str, query: Dict[str, List[str]]) -> Dict[str, object]:
    normalized_id = configured(evidence_id)
    if not normalized_id:
        raise ValueError("삭제할 근거 ID가 필요합니다.")
    store = stores.research_evidence_store()
    removed = False
    if hasattr(store, "retract_many_with_events"):
        mutation, recorded_events = store.retract_many_with_events(
            [normalized_id],
            "manual-evidence-retraction",
            lambda value: research_evidence_lifecycle_events({
                **(value.to_dict() if hasattr(value, "to_dict") else dict(value or {})),
                "status": "ok",
                "reason": "manual-evidence-retraction",
            }),
        )
        removed = bool(getattr(mutation, "retracted_count", 0) or 0)
        bridge = RealtimeEventBridge()
        for event in recorded_events:
            bridge.dispatch_recorded(event)
    else:
        removed = store.delete(normalized_id)
    if removed:
        new_domain_event(
            APP_ITEM_REMOVED,
            normalized_id,
            {"itemId": normalized_id, "type": "researchEvidence"},
        )
    payload = research_evidence_payload(query)
    payload["deleted"] = removed
    payload["deletedId"] = normalized_id
    return payload


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


def parse_utc(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    accounts = {account.account_id: account for account in stores.account_registry().load()}
    now_at = datetime.now(timezone.utc)
    if include_internal:
        message_types = list(dict.fromkeys(list(DEFAULT_CADENCE.keys()) + list(DEFAULT_NOTIFICATION_TEMPLATES.keys())))
    else:
        message_types = user_managed_notification_types()
    schedules = []
    for message_type in message_types:
        has_cadence = message_type in DEFAULT_CADENCE
        minutes = int(cadence.get(message_type, DEFAULT_CADENCE.get(message_type, 0)) or 0)
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
        if not has_cadence:
            status = "event"
        elif not enabled:
            status = "disabled"
        elif next_eligible_at and next_eligible_at > now_at:
            status = "waiting"
        else:
            status = "ready"
        if minutes:
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


def alert_event_public_payload(event) -> Dict[str, object]:
    context = alert_context(event)
    return {
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "messageType": event.rule,
        "rule": event.rule,
        "severity": event.severity,
        "symbol": event.symbol,
        "rawSymbol": context.get("rawSymbol") or event.symbol,
        "symbolName": context.get("symbolDisplayName") or "",
        "title": event.title,
        "lines": list(event.lines or []),
        "key": event.key,
    }


def selected_notification_test_account(payload: Dict[str, object]):
    requested = configured(payload.get("accountId") or payload.get("account_id"))
    accounts = stores.account_registry().load()
    if requested:
        for account in accounts:
            if account.account_id == requested:
                return account
        raise ValueError("요청한 계정을 찾지 못했습니다.")
    if not accounts:
        raise ValueError("테스트 발송에 사용할 계정이 없습니다.")
    return accounts[0]


def attach_notification_test_ontology_projection(snapshot, settings: Dict[str, str]) -> None:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    existing_projection = ontology.get("projection") or ontology.get("typedb")
    if isinstance(existing_projection, dict) and isinstance(existing_projection.get("inferenceBox"), dict):
        return
    try:
        recorder = PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(settings),
            quality_store=stores.ontology_quality_sample_store(settings),
            projection_run_store=stores.ontology_projection_run_store(settings),
            decision_episode_store=stores.investment_decision_episode_store(settings),
            hypothesis_proposal_store=stores.investment_research_store(settings),
            settings=settings,
            source="notification-test",
        )
        recorder.record_snapshot(snapshot)
    except Exception as error:  # noqa: BLE001 - test dispatch should report TypeDB readiness instead of crashing.
        snapshot.metadata.setdefault("ontology", {})["projection"] = {
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reason": "notification test TypeDB projection failed: " + str(error)[:160],
        }


def notification_test_event(message_type: str, snapshot):
    settings = runtime_settings()
    attach_notification_test_ontology_projection(snapshot, settings)
    monitor = RealtimeMonitor(settings)
    events = monitor.type_check_events_for_snapshot(snapshot)
    for event in events:
        if event.rule == message_type:
            return event
    for event in monitor.events_for_snapshot(snapshot, {}):
        if event.rule == message_type:
            return event
    return None


def notification_template_test_payload(payload: Dict[str, object]):
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    if not message_type:
        raise ValueError("messageType은 필요합니다.")
    dry_run = request_bool(payload.get("dryRun", payload.get("dry_run")))
    bypass_policy = (
        request_bool(payload.get("bypassPolicy", payload.get("bypass_policy")))
        or request_bool(payload.get("directSend", payload.get("direct_send")))
    )
    account = selected_notification_test_account(payload)
    snapshot = build_snapshot(account)
    if snapshot.mode != "live" and not payload.get("allowDemo"):
        return 409, {
            "delivered": False,
            "messageType": message_type,
            "error": "실제 토스 데이터를 가져오지 못했습니다: " + (snapshot.status or snapshot.mode),
            "snapshot": {
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "mode": snapshot.mode,
                "status": snapshot.status,
                "generatedAt": snapshot.generated_at,
            },
        }
    if message_type == "investmentInsight":
        missing_event = notification_test_event("ontologyInferenceMissing", snapshot)
        if missing_event:
            return 409, {
                "delivered": False,
                "messageType": message_type,
                "blockedBy": "ontologyInferenceMissing",
                "error": "온톨로지 추론 결과가 없어 투자 판단 테스트 발송을 막았습니다.",
                "event": alert_event_public_payload(missing_event),
            }
    event = notification_test_event(message_type, snapshot)
    if not event:
        return 422, {
            "delivered": False,
            "messageType": message_type,
            "error": "현재 데이터로 만들 수 있는 알림 이벤트가 없습니다.",
        }
    context = alert_context(event)
    context.update({
        "testDispatch": True,
        "notificationTestBypassPolicy": bypass_policy,
        "messageType": event.rule or message_type,
    })
    public_event = alert_event_public_payload(event)
    source_event = new_domain_event(
        NOTIFICATION_TEST_REQUESTED,
        event.key or message_type,
        {"messageType": message_type, "accountId": account.account_id, "accountLabel": account.label, "event": public_event},
    )
    message = notification_store().render(event.rule, context)
    job = NotificationJob.create(
        message,
        account_id=account.account_id,
        account_label=account.label,
        message_type=event.rule or message_type,
        source_event_id=source_event.event_id,
        source_event_name=source_event.name,
        context=context,
    )
    synchronous_test = bool(dry_run or bypass_policy)
    runner = build_notification_queue_runner(dry_run=synchronous_test)
    runner.apply_account_delivery_context(job, account)
    if synchronous_test:
        if str(job.message_type or "") == INVESTMENT_INSIGHT:
            explanation = build_customer_delivery_explanation(
                message_type=job.message_type,
                source_event_name=job.source_event_name,
                source_event_id=job.source_event_id,
                context=job.context,
            )
            validation = explanation.get("validation") if isinstance(explanation.get("validation"), dict) else {}
            if validation.get("state") != "valid":
                return 422, {
                    "delivered": False,
                    "messageType": message_type,
                    "error": "검증 알림의 발송 사유 계약을 만들지 못했습니다.",
                    "validation": validation,
                    "event": public_event,
                }
            test_context = dict(job.context or {})
            test_context.update({
                "customerDeliveryExplanation": explanation,
                "customerDeliveryExplanationRequired": True,
                "customerDeliveryExplanationValidationState": "valid",
            })
            job.context = test_context
        rendered_message = runner.render(job)
        if rendered_message:
            job.text = rendered_message
    if dry_run:
        return 200, {
            "delivered": False,
            "dryRun": True,
            "messageType": message_type,
            "direct": bypass_policy,
            "message": job.text,
            "event": alert_event_public_payload(event),
        }
    if bypass_policy:
        store = notification_queue_store()
        job.status = "processing"
        job.attempts = 1
        job.updated_at = utc_now_iso()
        store.upsert_job(job)
        try:
            runner.deliver(job, {account.account_id: account}, job.text)
            operator_detail = runner.capture_operator_report_after_delivery(job, job.text)
            store.mark_done(job)
            return 200, {
                "delivered": True,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "operatorReportStatus": job.context.get("operatorReasoningReportStatus"),
                "operatorReportJobId": job.context.get("operatorReasoningReportJobId"),
                "operatorReportDetail": operator_detail,
                "event": public_event,
            }
        except Exception as error:  # noqa: BLE001 - expose direct test failures to the UI.
            store.mark_failed(job, str(error))
            return 502, {
                "delivered": False,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "event": public_event,
                "error": str(error),
            }
    if not notification_queue_store().enqueue(job):
        if job.status == "suppressed":
            return 202, {
                "delivered": False,
                "queued": False,
                "suppressed": True,
                "provider": "Notification Queue",
                "messageType": message_type,
                "event": public_event,
                "deliveryDecision": (job.context or {}).get("deliveryDecision"),
                "deliveryGateState": (job.context or {}).get("deliveryGateState"),
                "reasons": (job.context or {}).get("deliveryReasons") or [],
                "error": job.last_error,
            }
        return 409, {
            "delivered": False,
            "queued": False,
            "provider": "Notification Queue",
            "messageType": message_type,
            "event": public_event,
            "error": "알림 작업을 큐에 적재하지 못했습니다.",
        }
    new_domain_event(
        NOTIFICATION_JOB_QUEUED,
        job.job_id,
        {
            "jobId": job.job_id,
            "messageType": job.message_type,
            "accountId": job.account_id,
            "sourceEventId": source_event.event_id,
        },
    )
    return 202, {
        "delivered": False,
        "queued": True,
        "jobId": job.job_id,
        "provider": "Notification Queue",
        "messageType": message_type,
        "event": public_event,
    }


def account_service() -> AccountApplicationService:
    registry = stores.account_registry()
    return AccountApplicationService(registry, registry.settings, event_publisher=RealtimeEventBridge())


def watchlist_refresh_status() -> Dict[str, object]:
    with WATCHLIST_REFRESH_LOCK:
        return {
            "status": "queued" if WATCHLIST_REFRESH_STATE["running"] else str(WATCHLIST_REFRESH_STATE["lastStatus"]),
            "running": bool(WATCHLIST_REFRESH_STATE["running"]),
            "pending": bool(WATCHLIST_REFRESH_STATE["pending"]),
            "accountIds": sorted(WATCHLIST_REFRESH_STATE["accountIds"]),
            "symbols": sorted(WATCHLIST_REFRESH_STATE["symbols"]),
            "lastError": str(WATCHLIST_REFRESH_STATE["lastError"]),
            "lastFinishedAt": str(WATCHLIST_REFRESH_STATE["lastFinishedAt"]),
        }


def run_watchlist_refresh_pipeline() -> None:
    while True:
        with WATCHLIST_REFRESH_LOCK:
            account_ids = set(WATCHLIST_REFRESH_STATE["accountIds"])
            symbols = set(WATCHLIST_REFRESH_STATE["symbols"])
            WATCHLIST_REFRESH_STATE["accountIds"] = set()
            WATCHLIST_REFRESH_STATE["symbols"] = set()
            WATCHLIST_REFRESH_STATE["pending"] = False
            WATCHLIST_REFRESH_STATE["lastStatus"] = "running"
            WATCHLIST_REFRESH_STATE["lastError"] = ""
        try:
            settings = runtime_settings()
            build_market_data_collection_runner(settings=settings).run_once(force=True)
            registry = stores.account_registry(settings)
            accounts = [account for account in registry.load() if not account_ids or account.account_id in account_ids]
            if accounts:
                build_monitor_runner(accounts, settings=settings).run_once(
                    force=False,
                    symbol_filter=symbols,
                    holdings_snapshot_requested=False,
                )
            with WATCHLIST_REFRESH_LOCK:
                WATCHLIST_REFRESH_STATE["lastStatus"] = "completed"
        except Exception as error:  # noqa: BLE001 - the saved watchlist must remain usable when a vendor is unavailable.
            report_runtime_error(operational_error_reporter(), "Watchlist refresh", error, "watchlist refresh pipeline")
            with WATCHLIST_REFRESH_LOCK:
                WATCHLIST_REFRESH_STATE["lastStatus"] = "failed"
                WATCHLIST_REFRESH_STATE["lastError"] = str(error)[:300]
        with WATCHLIST_REFRESH_LOCK:
            WATCHLIST_REFRESH_STATE["lastFinishedAt"] = now()
            if WATCHLIST_REFRESH_STATE["pending"]:
                continue
            WATCHLIST_REFRESH_STATE["running"] = False
            return


def request_watchlist_refresh(account_id: str, symbol: str, _action: str) -> Dict[str, object]:
    should_start = False
    with WATCHLIST_REFRESH_LOCK:
        WATCHLIST_REFRESH_STATE["accountIds"].add(str(account_id or ""))
        if symbol:
            WATCHLIST_REFRESH_STATE["symbols"].add(str(symbol).upper())
        WATCHLIST_REFRESH_STATE["pending"] = True
        if not WATCHLIST_REFRESH_STATE["running"]:
            WATCHLIST_REFRESH_STATE["running"] = True
            should_start = True
    if should_start:
        threading.Thread(target=run_watchlist_refresh_pipeline, name="watchlist-refresh", daemon=True).start()
    return watchlist_refresh_status()


def account_watchlist_service() -> AccountWatchlistService:
    registry = stores.account_registry()
    return AccountWatchlistService(
        registry,
        event_publisher=RealtimeEventBridge(),
        refresh_requester=request_watchlist_refresh,
    )


def symbol_universe_service():
    return build_symbol_universe_service()


def symbol_universe_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 16)
    offset = int(first_query(query, "offset") or 0)
    try:
        return symbol_universe_service().search(
            query=search,
            market=market,
            limit=limit,
            offset=offset,
        )
    except Exception as error:  # noqa: BLE001 - seed universe keeps search usable without optional MySQL.
        items = fallback_symbol_universe_items(search, market)
        return {
            "items": items[offset: offset + limit],
            "summary": fallback_symbol_universe_summary(str(error)[:240]),
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "offset": offset,
            "total": len(items),
            "storeWarning": str(error)[:240],
        }


def symbol_universe_suggest_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 8)
    try:
        return symbol_universe_service().suggest(
            query=search,
            market=market,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - autocomplete can fall back to local seed symbols.
        return {
            "items": fallback_symbol_universe_items(search, market)[:limit],
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "storeWarning": str(error)[:240],
        }


def fallback_symbol_universe_items(search: str = "", market: str = "") -> List[Dict[str, object]]:
    needle = configured(search).lower()
    market_filter = configured(market).upper()
    candidate_symbol_list = symbol_search_symbol_candidates(search)
    candidate_symbols = set(candidate_symbol_list)
    seed_symbols = list(DEFAULT_SYMBOL_SEEDS)
    for symbol in reversed(candidate_symbol_list):
        if symbol in seed_symbols:
            seed_symbols.remove(symbol)
        seed_symbols.insert(0, symbol)
    items = [seed_symbol(symbol).to_dict(24) for symbol in seed_symbols]
    if market_filter:
        items = [item for item in items if str(item.get("market") or "").upper() == market_filter]
    if needle:
        def matches(item):
            if str(item.get("symbol") or "").upper() in candidate_symbols:
                return True
            haystack = " ".join([
                str(item.get("symbol") or ""),
                str(item.get("name") or ""),
                str(item.get("market") or ""),
                str(item.get("sector") or ""),
            ]).lower()
            return needle in haystack

        items = [item for item in items if matches(item)]
    return items


def fallback_symbol_universe_summary(warning: str = "") -> Dict[str, object]:
    items = [seed_symbol(symbol).to_dict(24) for symbol in DEFAULT_SYMBOL_SEEDS]
    markets = []
    for market in sorted({str(item.get("market") or "") for item in items if item.get("market")}):
        markets.append({
            "market": market,
            "count": len([item for item in items if item.get("market") == market]),
            "lastSeenAt": "",
            "stale": True,
            "source": "Orbit Alpha seed",
            "sourceUrl": "local-default",
        })
    return {
        "markets": markets,
        "sources": [],
        "maxAgeHours": 24,
        "total": len(items),
        "storeWarning": warning,
    }


def requested_symbol_universe_markets(payload: Dict[str, object]) -> List[str]:
    raw_markets = payload.get("markets") if isinstance(payload, dict) else None
    if isinstance(raw_markets, str):
        requested = [item.strip().upper() for item in raw_markets.split(",") if item.strip()]
    elif isinstance(raw_markets, list):
        requested = [str(item or "").strip().upper() for item in raw_markets if str(item or "").strip()]
    else:
        requested = list(SUPPORTED_MARKETS)
    supported = [str(market or "").upper() for market in SUPPORTED_MARKETS]
    selected = [market for market in supported if market in requested]
    return selected or supported


def _symbol_universe_refresh_status_locked() -> Dict[str, object]:
    markets = [market for market in SUPPORTED_MARKETS if market in SYMBOL_UNIVERSE_REFRESH_STATE["markets"]]
    completed = [market for market in markets if market in SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]]
    total = len(markets)
    finished = len(completed)
    status = str(SYMBOL_UNIVERSE_REFRESH_STATE["status"] or "idle")
    stage = str(SYMBOL_UNIVERSE_REFRESH_STATE.get("stage") or "idle")
    stage_progress = {
        "queued": 3,
        "connecting": 10,
        "fetching": 35,
        "saving": 72,
        "verifying": 88,
        "summarizing": 94,
    }.get(stage, 0)
    progress = (
        100
        if status in {"completed", "partial", "failed"} and total
        else round(((finished * 100) + stage_progress) / total)
        if total
        else 0
    )
    return {
        "jobId": str(SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] or ""),
        "status": status,
        "running": bool(SYMBOL_UNIVERSE_REFRESH_STATE["running"]),
        "pending": bool(SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]),
        "markets": markets,
        "completedMarkets": completed,
        "completedCount": finished,
        "totalCount": total,
        "progressPercent": progress,
        "results": [dict(item) for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]],
        "summary": dict(SYMBOL_UNIVERSE_REFRESH_STATE["summary"]),
        "requestedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["requestedAt"] or ""),
        "startedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] or ""),
        "finishedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["finishedAt"] or ""),
        "lastError": str(SYMBOL_UNIVERSE_REFRESH_STATE["lastError"] or ""),
        "stage": stage,
        "currentMarket": str(SYMBOL_UNIVERSE_REFRESH_STATE.get("currentMarket") or ""),
        "stageItemCount": int(SYMBOL_UNIVERSE_REFRESH_STATE.get("stageItemCount") or 0),
        "updatedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE.get("updatedAt") or ""),
    }


def symbol_universe_refresh_status(job_id: str = "") -> Dict[str, object]:
    with SYMBOL_UNIVERSE_REFRESH_LOCK:
        status = _symbol_universe_refresh_status_locked()
    requested_job_id = configured(job_id)
    if requested_job_id and status["jobId"] and requested_job_id != status["jobId"]:
        status["requestedJobId"] = requested_job_id
        status["superseded"] = True
        return status
    if requested_job_id and not status["jobId"]:
        return {
            "jobId": requested_job_id,
            "status": "unknown",
            "running": False,
            "pending": False,
            "markets": [],
            "completedMarkets": [],
            "completedCount": 0,
            "totalCount": 0,
            "progressPercent": 0,
            "results": [],
            "summary": {},
            "requestedAt": "",
            "startedAt": "",
            "finishedAt": "",
            "lastError": "갱신 작업 상태를 찾을 수 없습니다. 서버가 재시작되었을 수 있습니다.",
            "latestJobId": "",
            "stage": "unknown",
            "currentMarket": "",
            "stageItemCount": 0,
            "updatedAt": "",
        }
    return status


def _replace_symbol_universe_market_result(result: Dict[str, object]) -> None:
    market = str(result.get("market") or "").upper()
    rows = [
        dict(item)
        for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]
        if str(item.get("market") or "").upper() != market
    ]
    rows.append(dict(result))
    order = {market_name: index for index, market_name in enumerate(SUPPORTED_MARKETS)}
    SYMBOL_UNIVERSE_REFRESH_STATE["results"] = sorted(
        rows,
        key=lambda item: order.get(str(item.get("market") or "").upper(), len(order)),
    )


def run_symbol_universe_refresh_pipeline(job_id: str) -> None:
    try:
        new_domain_event(
            SYMBOL_UNIVERSE_REFRESH_REQUESTED,
            job_id,
            {"jobId": job_id, "status": "running", "markets": symbol_universe_refresh_status(job_id)["markets"]},
        )
    except Exception as error:  # noqa: BLE001 - event transport cannot cancel the accepted refresh.
        report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, "refresh requested event")

    service = None
    while True:
        with SYMBOL_UNIVERSE_REFRESH_LOCK:
            if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                return
            batch = [
                market
                for market in SUPPORTED_MARKETS
                if market in SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]
                and market not in SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]
            ]
            SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"].difference_update(batch)
            SYMBOL_UNIVERSE_REFRESH_STATE["status"] = "running"
            SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] = SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] or now()
            SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = "connecting"
            SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = batch[0] if batch else ""
            SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = 0
            SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()

        for market in batch:
            try:
                if service is None:
                    service = symbol_universe_service()

                def update_progress(progress: Dict[str, object]) -> None:
                    with SYMBOL_UNIVERSE_REFRESH_LOCK:
                        if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                            return
                        SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = str(progress.get("stage") or "running")
                        SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = str(progress.get("market") or market)
                        SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = int(progress.get("count") or 0)
                        SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()

                payload = service.refresh([market], on_progress=update_progress)
                rows = [dict(item) for item in (payload.get("results") or []) if isinstance(item, dict)]
                result = next((item for item in rows if str(item.get("market") or "").upper() == market), None)
                result = result or {"market": market, "status": "error", "count": 0, "error": "갱신 결과가 없습니다."}
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            except Exception as error:  # noqa: BLE001 - one market must not stop the remaining catalog refresh.
                result = {"market": market, "status": "error", "count": 0, "error": str(error)[:300]}
                summary = {}
                report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, market)
            with SYMBOL_UNIVERSE_REFRESH_LOCK:
                if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                    return
                _replace_symbol_universe_market_result(result)
                SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"].add(market)
                SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = "market_completed"
                SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = market
                SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = int(result.get("count") or 0)
                SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()
                if summary:
                    SYMBOL_UNIVERSE_REFRESH_STATE["summary"] = dict(summary)

        with SYMBOL_UNIVERSE_REFRESH_LOCK:
            if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                return
            remaining = set(SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]).difference(
                SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]
            )
            if remaining:
                continue
            results = [dict(item) for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]]
            errors = [str(item.get("error") or item.get("status") or "error") for item in results if item.get("status") != "ok"]
            success_count = len([item for item in results if item.get("status") == "ok"])
            if errors and not success_count:
                final_status = "failed"
            elif errors:
                final_status = "partial"
            else:
                final_status = "completed"
            SYMBOL_UNIVERSE_REFRESH_STATE["status"] = final_status
            SYMBOL_UNIVERSE_REFRESH_STATE["running"] = False
            SYMBOL_UNIVERSE_REFRESH_STATE["finishedAt"] = now()
            SYMBOL_UNIVERSE_REFRESH_STATE["lastError"] = "; ".join(errors)[:500]
            SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = final_status
            SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = ""
            SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()
            final_payload = _symbol_universe_refresh_status_locked()
            break

    event_name = SYMBOL_UNIVERSE_REFRESH_FAILED if final_payload["status"] == "failed" else SYMBOL_UNIVERSE_REFRESHED
    try:
        new_domain_event(event_name, job_id, final_payload)
    except Exception as error:  # noqa: BLE001 - status polling remains available when event delivery fails.
        report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, "refresh completion event")


def request_symbol_universe_refresh(payload: Dict[str, object]) -> Dict[str, object]:
    markets = requested_symbol_universe_markets(payload)
    should_start = False
    coalesced = False
    with SYMBOL_UNIVERSE_REFRESH_LOCK:
        if SYMBOL_UNIVERSE_REFRESH_STATE["running"]:
            coalesced = True
        else:
            SYMBOL_UNIVERSE_REFRESH_STATE.update({
                "jobId": new_id("symbol-refresh"),
                "running": True,
                "status": "queued",
                "markets": set(),
                "pendingMarkets": set(),
                "completedMarkets": set(),
                "results": [],
                "summary": {},
                "requestedAt": now(),
                "startedAt": "",
                "finishedAt": "",
                "lastError": "",
                "stage": "queued",
                "currentMarket": "",
                "stageItemCount": 0,
                "updatedAt": now(),
            })
            should_start = True
        SYMBOL_UNIVERSE_REFRESH_STATE["markets"].update(markets)
        SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"].update(markets)
        job_id = str(SYMBOL_UNIVERSE_REFRESH_STATE["jobId"])
        status = _symbol_universe_refresh_status_locked()
    if should_start:
        threading.Thread(
            target=run_symbol_universe_refresh_pipeline,
            args=(job_id,),
            name="symbol-universe-refresh",
            daemon=True,
        ).start()
    return {**status, "accepted": True, "coalesced": coalesced}


def refresh_symbol_universe_payload(payload: Dict[str, object]) -> Dict[str, object]:
    markets = requested_symbol_universe_markets(payload)
    result = symbol_universe_service().refresh(markets)
    new_domain_event(
        SYMBOL_UNIVERSE_REFRESHED,
        ",".join(markets or []) or "all",
        {"status": "completed", "summary": result.get("summary") or {}, "markets": markets or []},
    )
    return result


def service_accounts_payload() -> Dict[str, object]:
    return {"accounts": account_service().list_masked()}


def save_account_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return {"account": account_service().save_payload(payload).masked()}


def remove_account_payload(account_id: str) -> Dict[str, object]:
    return {"removed": account_service().remove(account_id), "id": account_id}


def account_watchlist_payload(account_id: str) -> Dict[str, object]:
    return account_watchlist_service().list_payload(account_id)


def add_account_watchlist_payload(account_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return account_watchlist_service().add(account_id, (payload or {}).get("symbol"))


def replace_account_watchlist_payload(account_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    symbols = (payload or {}).get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("symbols 배열이 필요합니다.")
    return account_watchlist_service().replace(account_id, symbols)


def remove_account_watchlist_payload(account_id: str, symbol: str) -> Dict[str, object]:
    return account_watchlist_service().remove(account_id, symbol)


def share_denied_page() -> str:
    return "".join([
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>Orbit Alpha 접근 제한</title>",
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f4ee;color:#171717}main{max-width:520px;padding:32px;line-height:1.6}h1{font-size:22px;margin:0 0 10px}p{margin:0;color:#5f5a53}</style>",
        "</head><body><main><h1>공유 접근 토큰이 필요합니다.</h1>",
        "<p>서버를 공유한 사람이 제공한 전체 URL로 다시 접속하세요.</p>",
        "</main></body></html>",
    ])


def parse_number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def fetch_text(target_url: str, timeout: int = 8, headers: Dict[str, str] = None) -> str:
    def fetch() -> str:
        request = urllib.request.Request(
            target_url,
            headers={"User-Agent": "OrbitAlpha/0.1", **(headers or {})},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    return guarded_external_call(
        runtime_settings(),
        web_proxy_source_for_url(target_url),
        external_call_target(target_url),
        fetch,
        state=WEB_PROXY_API_GUARD_STATE,
        rate_limit_seconds=0,
    )


def fetch_json_url(target_url: str, timeout: int = 8, headers: Dict[str, str] = None):
    return json.loads(fetch_text(target_url, timeout=timeout, headers={"Accept": "application/json", **(headers or {})}))


def web_proxy_source_for_url(target_url: str) -> str:
    host = urllib.parse.urlparse(str(target_url or "")).netloc.lower()
    if "stlouisfed.org" in host:
        return "FRED"
    if "opendart.fss.or.kr" in host:
        return "OpenDART"
    if "m.stock.naver.com" in host:
        return "Naver Finance"
    if "stooq.com" in host:
        return "Stooq"
    return "Web Proxy"


def normalize_fred_observations_url(query: Dict[str, List[str]]) -> str:
    series_id = configured(first_query(query, "series_id")).upper()
    api_key = configured(first_query(query, "api_key"))
    limit = configured(first_query(query, "limit") or "1")
    sort_order = configured(first_query(query, "sort_order") or "desc").lower()
    if not re.match(r"^[A-Z0-9_.-]{1,40}$", series_id):
        raise ValueError("FRED series_id 형식이 올바르지 않습니다.")
    if not re.match(r"^[A-Za-z0-9]{16,64}$", api_key):
        raise ValueError("FRED_API_KEY 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{1,4}$", limit):
        raise ValueError("FRED limit 형식이 올바르지 않습니다.")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("FRED sort_order는 asc 또는 desc만 가능합니다.")
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    })
    return "https://api.stlouisfed.org/fred/series/observations?" + params


def normalize_opendart_company_url(query: Dict[str, List[str]]) -> str:
    api_key = configured(first_query(query, "crtfc_key"))
    corp_code = configured(first_query(query, "corp_code") or "00126380")
    if not re.match(r"^[A-Za-z0-9]{32,64}$", api_key):
        raise ValueError("OpenDART API key 형식이 올바르지 않습니다.")
    if not re.match(r"^\d{8}$", corp_code):
        raise ValueError("OpenDART corp_code 형식이 올바르지 않습니다.")
    return "https://opendart.fss.or.kr/api/company.json?" + urllib.parse.urlencode({
        "crtfc_key": api_key,
        "corp_code": corp_code,
    })


def first_query(query: Dict[str, List[str]], key: str) -> str:
    value = query.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value or "")


def compact_flow_lens_payload(payload: Dict[str, object]) -> Dict[str, object]:
    """Keep initial dashboard payload small; full ontology rows load on demand."""
    if not isinstance(payload, dict):
        return payload
    compact = dict(payload)
    decision = compact.get("tossDecision")
    if not isinstance(decision, dict):
        return compact
    decision = dict(decision)
    compact["tossDecision"] = decision

    market_item_keys = {
        "symbol", "name", "symbolName", "displayName", "market", "exchange", "currency", "sector",
        "source", "currentPrice", "changeRate", "quantity", "averagePrice", "marketValue",
        "marketValueKrw", "profitLoss", "profitLossRate", "ma5", "ma20", "ma60", "volume",
        "volumeRatio", "tradeStrength", "buyVolume", "sellVolume", "bidAskImbalance",
        "foreignBuyVolume", "foreignSellVolume", "foreignNet", "foreignNetVolume",
        "institutionBuyVolume", "institutionSellVolume", "institutionNet", "institutionNetVolume", "individualNet",
        "individualNetVolume", "marketSignalCoverage", "freshnessStatus", "quoteStatus", "quoteSource",
        "provider", "sourceAsOf", "updatedAt", "dataQuality", "dataMode", "isMock",
    }
    decision_item_keys = market_item_keys | {
        "decision", "action", "actionCode", "reviewLevel", "reason", "nextAction", "decisionBasis",
        "portfolioRole", "accountId", "accountLabel", "decisionKey", "decisionEpisodeId", "updatedAt",
    }

    def compact_rows(value, allowed_keys):
        if not isinstance(value, list):
            return value
        return [
            {key: item.get(key) for key in sorted(allowed_keys) if key in item}
            for item in value
            if isinstance(item, dict)
        ]

    toss = compact.get("toss")
    if isinstance(toss, dict):
        toss = dict(toss)
        for key in ["positions", "watchlistQuotes", "watchlist"]:
            toss[key] = compact_rows(toss.get(key), market_item_keys)
        external = toss.get("externalSignals")
        if isinstance(external, dict):
            external = dict(external)
            omitted = []
            for key in [
                "yfinanceData",
                "researchEvidence",
                "companyOverviews",
                "earningsReports",
                "secFilings",
                "dartDisclosures",
                "companyKnowledge",
            ]:
                value = external.pop(key, None)
                if value not in (None, [], {}, ""):
                    omitted.append(key)
                    if isinstance(value, (list, dict)):
                        external[key + "Count"] = len(value)
            external["detailLevel"] = "summary"
            external["heavyFieldsOmitted"] = omitted
            toss["externalSignals"] = external
        metadata = toss.get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            proxies = metadata.pop("marketProxyQuotes", None)
            if isinstance(proxies, (list, dict)):
                metadata["marketProxyQuoteCount"] = len(proxies)
            omitted_metadata = []
            for key in ["cryptoTransitionBaseline", "marketObservationBaselines", "ontology", "kis"]:
                value = metadata.pop(key, None)
                if value not in (None, [], {}, ""):
                    omitted_metadata.append(key)
            metadata["detailLevel"] = "summary"
            metadata["heavyFieldsOmitted"] = omitted_metadata
            toss["metadata"] = metadata
        compact["toss"] = toss

    portfolio = compact.get("portfolio")
    if isinstance(portfolio, dict):
        portfolio = dict(portfolio)
        portfolio["positions"] = compact_rows(portfolio.get("positions"), market_item_keys)
        compact["portfolio"] = portfolio

    for key in ["positions", "items"]:
        decision[key] = compact_rows(decision.get(key), decision_item_keys)

    strategy = decision.get("ontologyStrategy")
    if isinstance(strategy, dict):
        omitted = []
        strategy = dict(strategy)
        for key in [
            "prompt",
            "tbox",
            "aiInferencePacket",
            "reasoningCards",
            "entities",
            "relations",
            "tboxEntities",
            "tboxRelations",
            "aboxEntities",
            "aboxRelations",
            "evidence",
            "beliefs",
            "opinions",
            "activeInvestmentOpinions",
            "executionPlans",
            "insights",
            "dataQuality",
        ]:
            value = strategy.pop(key, None)
            if value not in (None, [], {}, ""):
                omitted.append(key)
                if isinstance(value, list):
                    strategy[key + "Count"] = len(value)
        strategy["detailLevel"] = "summary"
        strategy["detailAvailable"] = True
        strategy["heavyFieldsOmitted"] = omitted
        decision["ontologyStrategy"] = strategy

    analysis = decision.get("investmentAnalysis")
    if isinstance(analysis, dict):
        analysis = dict(analysis)
        reasoning_cards = analysis.pop("reasoningCards", None)
        if isinstance(reasoning_cards, list):
            analysis["reasoningCardCount"] = len(reasoning_cards)
        analysis["actionQueue"] = compact_rows(analysis.get("actionQueue"), decision_item_keys)
        analysis["detailLevel"] = "summary"
        analysis["detailAvailable"] = True
        decision["investmentAnalysis"] = analysis

    # The same decision graph is also projected at the root for the compact
    # dashboard.  Strip only duplicated explanatory packets here; the board,
    # queue and lineage remain available to the initial screen.
    root_analysis = compact.get("investmentAnalysis")
    if isinstance(root_analysis, dict):
        root_analysis = dict(root_analysis)
        omitted = []
        for key in ["reasoningCards", "aiInferencePacket", "entities", "relations", "evidence", "beliefs", "opinions"]:
            value = root_analysis.pop(key, None)
            if value not in (None, [], {}, ""):
                omitted.append(key)
                if isinstance(value, list):
                    root_analysis[key + "Count"] = len(value)
        root_analysis["actionQueue"] = compact_rows(root_analysis.get("actionQueue"), decision_item_keys)
        root_analysis["detailLevel"] = "summary"
        root_analysis["detailAvailable"] = True
        root_analysis["heavyFieldsOmitted"] = omitted
        compact["investmentAnalysis"] = root_analysis

    compact["payloadDetail"] = "summary"
    compact["fullDetailPath"] = "/api/flow-lens?detail=full"
    return compact


def persisted_flow_lens_snapshot(watchlist_symbols: str = "") -> Dict[str, object]:
    """Read the latest verified monitor projection without calling vendors."""
    try:
        settings = operational_read_settings()
        states = stores.monitor_store(settings).previous
        candidates = [item for item in states.values() if isinstance(item, dict) and item]
        if not candidates:
            return {}
        latest = sorted(candidates, key=lambda item: str(item.get("generatedAt") or ""), reverse=True)[0]
        return build_flow_lens_service(settings).snapshot_from_monitor_state(
            latest,
            watchlist_symbols=watchlist_symbols,
        )
    except Exception:
        return {}


def flow_lens_read_model() -> FlowLensReadModel:
    global FLOW_LENS_READ_MODEL
    with FLOW_LENS_READ_MODEL_LOCK:
        if FLOW_LENS_READ_MODEL is None:
            def refresh_snapshot(mock: bool, watchlist_symbols: str) -> Dict[str, object]:
                return flow_lens_snapshot(mock=mock, watchlist_symbols=watchlist_symbols)

            def notify_ready(snapshot: Dict[str, object]) -> None:
                REALTIME_HUB.broadcast("dashboard.snapshot_ready", {
                    "generatedAt": snapshot.get("generatedAt"),
                    "dataMode": snapshot.get("dataMode"),
                })

            FLOW_LENS_READ_MODEL = FlowLensReadModel(
                snapshot_provider=refresh_snapshot,
                persisted_provider=persisted_flow_lens_snapshot,
                on_refresh=notify_ready,
            )
        return FLOW_LENS_READ_MODEL


def flow_lens_read_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
    detail = configured(first_query(query, "detail") or first_query(query, "view")).lower()
    refresh = request_bool(first_query(query, "refresh"), False)
    result = flow_lens_read_model().read(
        mock=mock_value in {"1", "true", "mock"},
        watchlist_symbols=first_query(query, "watchlistSymbols"),
        refresh=refresh,
    )
    if not result.snapshot:
        return {
            "generatedAt": now(),
            "dataMode": "pending",
            "readModel": result.metadata(),
            "portfolio": {},
            "tossDecision": {},
        }
    payload = dict(result.snapshot)
    payload["readModel"] = result.metadata()
    payload["dataFreshness"] = flow_lens_data_freshness(payload.get("generatedAt"), runtime_settings())
    try:
        payload["capitalFlow"] = capital_flow_api_payload(query, snapshot=payload)
    except Exception as error:  # noqa: BLE001 - portfolio snapshot remains available when the analytical store is down.
        payload["capitalFlow"] = {
            "contract": "capital-flow-summary-v1",
            "status": "unavailable",
            "error": str(error)[:240],
            "markets": [],
            "sectors": [],
            "subjects": [],
        }
    if detail not in {"full", "detail", "all"}:
        payload = compact_flow_lens_payload(payload)
        payload["readModel"] = result.metadata()
    return payload


def console_read_model_service(settings: Dict[str, object] = None) -> ConsoleReadModelService:
    configured_settings = settings or operational_read_settings()
    return ConsoleReadModelService(symbol_repository=stores.symbol_universe_store(configured_settings))


def _console_dashboard_source_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = operational_read_settings()
    account_id = first_query(query, "accountId") or "default"

    def lifecycle_payload():
        return stores.investment_domain_store(settings).latest_portfolio_lifecycle("portfolio:" + account_id)

    def cases_payload():
        return investment_case_api_payload({"accountId": [account_id], "limit": ["100"]})

    def calendar_payload():
        calendar_query = {
            "from": [datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")],
            "limit": ["40"],
        }
        return investment_calendar_payload(calendar_query)

    readers = {
        "snapshot": lambda: flow_lens_read_payload(query),
        "lifecycle": lifecycle_payload,
        "cases": cases_payload,
        "calendar": calendar_payload,
    }
    results = {}
    with ThreadPoolExecutor(max_workers=len(readers), thread_name_prefix="console-dashboard") as executor:
        futures = {key: executor.submit(reader) for key, reader in readers.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as error:  # noqa: BLE001 - dashboard sections degrade independently.
                results[key] = {"status": "unavailable", "error": str(error)[:240]}
    return console_read_model_service(settings).dashboard_summary(
        results.get("snapshot") or {},
        results.get("lifecycle") or {},
        results.get("cases") or {"items": []},
        results.get("calendar") or {"events": []},
    )


def console_dashboard_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    watchlist = ",".join(sorted(filter(None, first_query(query, "watchlistSymbols").upper().split(","))))
    cache_key = account_id + "|" + watchlist
    return cached_api_payload(
        DASHBOARD_READ_MODEL,
        cache_key,
        lambda: _console_dashboard_source_payload(query),
        force=request_bool(first_query(query, "refresh"), False),
    )


def console_portfolio_api_payload(query: Dict[str, List[str]], view: str) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    portfolio_id = first_query(query, "portfolioId") or "portfolio:" + account_id
    cache_key = "|".join([
        "portfolio-console-v2",
        account_id,
        portfolio_id,
        str(view or "summary"),
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        lifecycle = stores.investment_domain_store(settings).latest_portfolio_lifecycle(portfolio_id)
        snapshot = flow_lens_read_payload(query) if view in {"summary", "positions"} else {}
        subject_case = (
            stores.subject_decision_case_store(settings).latest_portfolio(account_id)
            if view in {"summary", "rebalance", "interpretation"}
            else None
        )
        return console_read_model_service(settings).portfolio(
            lifecycle,
            view,
            snapshot=snapshot,
            subject_case=subject_case.to_dict() if subject_case else {},
        )

    payload = cached_api_payload(
        PORTFOLIO_CONSOLE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("view", str(view or "summary"))
    payload.setdefault("summary", {})
    if view in {"summary", "positions"}:
        payload.setdefault("positions", [])
    return payload


def console_market_instruments_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    watchlist = ",".join(sorted(filter(None, first_query(query, "watchlistSymbols").upper().split(","))))

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        snapshot = flow_lens_read_payload(query)
        read_model = snapshot.get("readModel") if isinstance(snapshot.get("readModel"), dict) else {}
        if str(read_model.get("status") or "").lower() == "pending" or not read_model.get("ready", True):
            raise RuntimeError("시장 스냅샷 읽기 모델을 준비하고 있습니다.")
        return console_read_model_service(settings).market_instruments(snapshot)

    payload = cached_api_payload(
        MARKET_INSTRUMENTS_READ_MODEL,
        watchlist or "default",
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("items", [])
    payload.setdefault("summary", {})
    return payload


def console_market_evidence_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    requested_limit = safe_int(first_query(query, "limit"), 12, 1, 100)
    source_query = {key: list(value) for key, value in query.items()}
    source_query["limit"] = [str(min(100, max(16, requested_limit * 2)))]
    cache_key = "|".join([
        str(first_query(query, "symbol") or "all").upper(),
        str(first_query(query, "kind") or "all"),
        str(requested_limit),
    ])

    def load() -> Dict[str, object]:
        payload = research_evidence_payload(source_query)
        return console_read_model_service().market_evidence(payload, requested_limit)

    payload = cached_api_payload(
        MARKET_EVIDENCE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("items", [])
    payload.setdefault("totalEligible", 0)
    return payload


def console_decisions_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    cache_key = "|".join([
        str(first_query(query, "accountId") or first_query(query, "account") or "default"),
        str(first_query(query, "symbol") or "all").upper(),
        str(first_query(query, "limit") or "100"),
        str(first_query(query, "audience") or first_query(query, "includeOperator") or "user"),
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        payload = investment_case_api_payload(query)
        return console_read_model_service(settings).decision_heads(payload)

    return cached_api_payload(
        DECISION_LIST_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_episodes_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 50, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "episodes|" + "|".join([account_id or "all", symbol or "all", str(limit), view]),
        lambda: build_investment_brain_service().episodes(
            account_id=account_id,
            symbol=symbol,
            limit=limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_hypothesis_lifecycles_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 100, 1, 500)
    event_limit = safe_int(first_query(query, "eventLimit"), 100, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    market_id = str(first_query(query, "marketId") or "")
    scope = str(first_query(query, "scope") or "")
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "lifecycles|" + "|".join([
            account_id or "all", symbol or "all", market_id or "all", scope or "all",
            str(limit), str(event_limit), view,
        ]),
        lambda: build_investment_brain_service().hypothesis_lifecycles(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=limit,
            event_limit=event_limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_hypothesis_workspace_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 100, 1, 500)
    event_limit = safe_int(first_query(query, "eventLimit"), 100, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    market_id = str(first_query(query, "marketId") or "")
    scope = str(first_query(query, "scope") or "")
    view = str(first_query(query, "view") or "summary")
    cache_key = "|".join([
        account_id or "all", symbol or "all", market_id or "all", scope or "all",
        str(limit), str(event_limit), view,
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        return build_investment_brain_service(settings).hypothesis_workspace(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=limit,
            event_limit=event_limit,
            view=view,
        )

    return cached_api_payload(
        HYPOTHESIS_WORKSPACE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )


def investment_brain_research_runs_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 50, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "research-runs|" + "|".join([account_id or "all", symbol or "all", str(limit), view]),
        lambda: build_investment_brain_service().research_runs(
            account_id=account_id,
            symbol=symbol,
            limit=limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def _console_operations_health_source_payload() -> Dict[str, object]:
    settings = operational_read_settings()

    def storage_payload():
        from ..domain.mysql_minimal_retention import mysql_minimal_retention_policy
        from ..domain.operational_storage_capacity import operational_storage_capacity_read_model
        from .operational_store import operational_storage_capacity_state_store
        from .operational_storage_guard import operational_storage_inventory

        inventory = operational_storage_inventory(settings)
        try:
            stored = dict(
                operational_storage_capacity_state_store(settings).load() or {}
            )
            observation = dict(stored.get("operationalStorageCapacity") or {})
        except Exception:  # Capacity inventory remains useful during state-store recovery.
            observation = {}
        inventory.update(
            operational_storage_capacity_read_model(inventory, observation)
        )
        policy = mysql_minimal_retention_policy(settings)
        inventory["retentionPolicy"] = {
            "typedbActiveHours": int(settings.get("typedbDataRetentionHours") or 72),
            "typedbWalTriggerMb": int(settings.get("typedbCapacityAutoRotateWalMb") or 4096),
            "typedbRollbackMinutes": int(settings.get("typedbBlueGreenRetiredRetentionMinutes") or 120),
            "notificationPayloadDays": round(policy.terminal_notification_retention_hours / 24),
            "completedWorldProjectionHours": policy.completed_world_projection_retention_hours,
            "completedInferenceDetailDays": round(policy.completed_inference_detail_retention_hours / 24),
            "reasoningCaseDays": round(policy.investment_reasoning_case_retention_hours / 24),
            "statisticalSignalDays": round(policy.statistical_model_signal_snapshot_retention_hours / 24),
            "timeSeriesDays": dict(policy.market_time_series_retention_days),
        }
        return inventory

    readers = {
        "realtime": realtime_status_payload,
        "external": external_data_status_payload,
        "reasoning": ontology_reasoning_status_payload,
        "engine": reasoning_engine_platform_status_payload,
        "timeSeries": time_series_platform_status_payload,
        "storage": storage_payload,
    }
    payloads = {}
    executor = ThreadPoolExecutor(max_workers=len(readers), thread_name_prefix="console-health")
    futures = {key: executor.submit(reader) for key, reader in readers.items()}
    _completed, pending = wait(futures.values(), timeout=8)
    for key, future in futures.items():
        if future in pending:
            payloads[key] = {"status": "unavailable", "error": "status read timed out after 8 seconds"}
            future.cancel()
            continue
        try:
            payloads[key] = future.result()
        except Exception as error:  # noqa: BLE001 - one status source cannot hide the others.
            payloads[key] = {"status": "unavailable", "error": str(error)[:240]}
    executor.shutdown(wait=False, cancel_futures=True)
    return console_read_model_service().operations_health(payloads)


def console_operations_health_api_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        OPERATIONS_HEALTH_READ_MODEL,
        "all",
        _console_operations_health_source_payload,
        force=force,
    )


def category_for(value: str) -> str:
    text = str(value or "")
    if re.search(r"주식|투자|종목|포트폴리오|배당|매수|매도", text):
        return "finance"
    if re.search(r"자산|현금|계좌|예산|지출|저축|대출", text):
        return "asset"
    if re.search(r"여행|항공|호텔|숙소|동선|예약", text):
        return "travel"
    if re.search(r"일정|회의|약속|마감|캘린더|할 일", text):
        return "schedule"
    if re.search(r"좋아|싫어|선호|말투|스타일|방식", text):
        return "preference"
    if re.search(r"나는|내가|나의|목표|직업|역할", text):
        return "identity"
    return "other"


def normalize_amount(value):
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def normalize_item_fields(fields) -> Dict[str, str]:
    if not isinstance(fields, dict):
        return {}
    return {str(key): "" if value is None else str(value).strip() for key, value in fields.items()}


def patch_item(item: Dict[str, object], body: Dict[str, object]) -> Dict[str, object]:
    next_item = dict(item)
    if body.get("type") in DOMAIN_TYPES:
        next_item["type"] = body["type"]
    if "title" in body and configured(body.get("title")):
        next_item["title"] = configured(body.get("title"))
    if "status" in body:
        next_item["status"] = configured(body.get("status")) or "open"
    if "date" in body:
        next_item["date"] = configured(body.get("date"))
    if "amount" in body:
        next_item["amount"] = normalize_amount(body.get("amount"))
    if "currency" in body:
        next_item["currency"] = configured(body.get("currency"))
    if "ticker" in body:
        next_item["ticker"] = configured(body.get("ticker")).upper()
    if "location" in body:
        next_item["location"] = configured(body.get("location"))
    if "notes" in body:
        next_item["notes"] = configured(body.get("notes"))
    if "fields" in body:
        next_item["fields"] = {**dict(next_item.get("fields") or {}), **normalize_item_fields(body.get("fields"))}
    next_item["updatedAt"] = now()
    return next_item


def fallback_reply(message: str) -> str:
    text = configured(message)
    if re.search(r"주식|투자|종목|포트폴리오", text):
        return "투자 판단은 매수/매도 단정보다 가격 기준, 손절 기준, 보유 이유, 현금 비중을 나눠 확인하겠습니다."
    if re.search(r"일정|회의|약속|마감", text):
        return "일정은 오늘 처리할 일, 미룰 일, 의존성이 있는 일을 분리해서 정리하겠습니다."
    if re.search(r"여행|항공|호텔|숙소", text):
        return "여행 계획은 날짜, 예산, 이동 동선, 예약 마감일을 기준으로 정리하겠습니다."
    return "기록했습니다. 필요한 내용을 주식, 여행, 자산, 일정 중 어느 쪽으로 정리할지 알려주면 다음 행동으로 나누겠습니다."


def local_memory_candidates(message: str) -> List[Dict[str, object]]:
    text = configured(message)
    if len(text) < 12:
        return []
    signals = ["나는", "내가", "나의", "선호", "좋아", "싫어", "원해", "중요", "성향", "스타일", "방식", "투자", "여행", "일정", "자산", "목표"]
    if not any(signal in text for signal in signals):
        return []
    normalized = re.sub(r"^(나는|내가|나의)\s*", "", text).strip()
    return [{
        "content": ("사용자는 " + normalized)[:180],
        "category": category_for(text),
        "importance": 4 if re.search(r"선호|싫어|좋아|중요|원해|성향|방식|스타일", text) else 3,
    }]


def memory_fingerprint(content: str) -> str:
    return re.sub(r"[.,!?'\"]", "", re.sub(r"\s+", "", str(content or "").lower())).removeprefix("사용자는")


def persist_memory_candidates(candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
    saved = []
    if not candidates:
        return saved

    def mutate(store):
        for candidate in candidates[:3]:
            content = configured(candidate.get("content"))
            if len(content) < 5:
                continue
            category = candidate.get("category") if candidate.get("category") in MEMORY_CATEGORIES else category_for(content)
            next_fingerprint = memory_fingerprint(content)
            duplicate = any(
                memory.get("status") != "archived"
                and memory.get("category") == category
                and memory_fingerprint(memory.get("content")).find(next_fingerprint) >= 0
                for memory in store["memories"]
            )
            if duplicate:
                continue
            stamped = now()
            memory = {
                "id": new_id("mem"),
                "content": content,
                "category": category,
                "status": "approved",
                "importance": max(1, min(5, int(candidate.get("importance") or 3))),
                "source": "conversation",
                "createdAt": stamped,
                "updatedAt": stamped,
            }
            store["memories"].insert(0, memory)
            saved.append(memory)

    save_store(mutate)
    if saved:
        new_domain_event(
            APP_MEMORY_RECORDED,
            "conversation",
            {"count": len(saved), "memoryIds": [item.get("id") for item in saved], "source": "conversation"},
        )
    return saved


def append_message(role: str, content: str) -> Dict[str, object]:
    message = {}

    def mutate(store):
        message.update({"id": new_id("msg"), "role": role, "content": content, "createdAt": now()})
        store["messages"].append(message)
        store["messages"] = store["messages"][-80:]

    save_store(mutate)
    new_domain_event(
        CHAT_MESSAGE_APPENDED,
        message.get("id") or role,
        {"messageId": message.get("id"), "role": role},
    )
    return message


def run_local_codex(message: str) -> str:
    if os.environ.get("LOCAL_CODEX_ENABLED") == "0":
        return ""
    codex = os.environ.get("CODEX_BIN") or "codex"
    prompt = "\n".join([
        "너는 Orbit Alpha 웹앱의 로컬 Python 비서 백엔드다.",
        "한국어로 답하고, 투자 관련 답변은 확인할 데이터와 리스크 중심으로만 말한다.",
        "파일을 수정하지 말고 설명만 한다.",
        "",
        "사용자 질문:",
        message,
    ])
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as output:
        output_path = output.name
    try:
        result = subprocess.run(
            [
                codex,
                *codex_cli_arguments(),
                "-a",
                "never",
                "--sandbox",
                "read-only",
                "--cd",
                str(ROOT_DIR),
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-last-message",
                output_path,
                "-",
            ],
            input=prompt,
            text=True,
            cwd=str(ROOT_DIR),
            env={**os.environ, "NO_COLOR": "1"},
            timeout=int(os.environ.get("CODEX_TIMEOUT_MS") or "90000") / 1000,
            capture_output=True,
        )
        if result.returncode != 0:
            return ""
        return Path(output_path).read_text(encoding="utf-8").strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    finally:
        try:
            Path(output_path).unlink()
        except OSError:
            pass


def is_investment_brain_question(message: str, body: Dict[str, object] = None) -> bool:
    body = body if isinstance(body, dict) else {}
    if configured(body.get("mode") or body.get("engine")).lower() in {"investment", "ontology", "investment-brain"}:
        return True
    compact = str(message or "").lower()
    return any(term in compact for term in [
        "주식", "종목", "매수", "매도", "보유", "추가매수", "분할축소", "손절",
        "포트폴리오", "수익률", "투자", "리스크", "공시", "주가", "증권",
    ])


def investment_brain_question_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message") or body.get("question"))
    if not message:
        raise ValueError("투자 질문을 입력하세요.")
    result = build_investment_brain_service().ask(
        message,
        account_id=configured(body.get("accountId")),
        symbol=configured(body.get("symbol")),
    )
    return result


def chat_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message"))
    if not message:
        raise ValueError("메시지를 입력하세요.")
    append_message("user", message)
    if is_investment_brain_question(message, body):
        result = investment_brain_question_payload(body)
        append_message("assistant", str(result.get("reply") or ""))
        return result
    reply = run_local_codex(message) or fallback_reply(message)
    candidates = persist_memory_candidates(local_memory_candidates(message))
    append_message("assistant", reply)
    return {"reply": reply, "memoryCandidates": candidates, "usedFallback": True, "engine": "python"}


def stock_input_to_naver_code(symbol: str) -> str:
    match = re.match(r"^(\d{6})(?:\.(KS|KQ|KR))?$", configured(symbol).upper())
    return match.group(1) if match else ""


def stock_input_to_stooq_symbol(symbol: str) -> str:
    cleaned = configured(symbol).upper()
    if not cleaned or stock_input_to_naver_code(cleaned):
        return ""
    return cleaned if "." in cleaned else cleaned + ".US"


def fetch_naver_quote(symbol: str) -> Dict[str, object]:
    code = stock_input_to_naver_code(symbol)
    payload = fetch_json_url("https://m.stock.naver.com/api/stock/" + code + "/basic")
    price = parse_number(payload.get("closePrice"))
    if price is None:
        raise ValueError("국내 종목 가격을 찾지 못했습니다.")
    return {
        "inputSymbol": symbol,
        "symbol": code,
        "displaySymbol": code,
        "name": payload.get("stockName") or code,
        "exchange": payload.get("stockExchangeName") or "KR",
        "currency": "KRW",
        "price": price,
        "previousClose": None,
        "change": parse_number(payload.get("compareToPreviousClosePrice")),
        "changePercent": parse_number(payload.get("fluctuationsRatio")),
        "open": None,
        "high": None,
        "low": None,
        "volume": parse_number(payload.get("accumulatedTradingVolume")),
        "marketStatus": payload.get("marketStatus") or "",
        "asOf": payload.get("localTradedAt") or "",
        "source": "Naver Finance",
    }


def fetch_stooq_quote(symbol: str) -> Dict[str, object]:
    stooq_symbol = stock_input_to_stooq_symbol(symbol)
    raw = fetch_text("https://stooq.com/q/l/?s=" + urllib.parse.quote(stooq_symbol.lower()) + "&f=sd2t2ohlcvpn&h&e=csv")
    rows = raw.strip().splitlines()
    if len(rows) < 2:
        raise ValueError("해외 종목 가격을 찾지 못했습니다.")
    header = next(csv.reader([rows[0]]))
    values = next(csv.reader([rows[1]]))
    row = dict(zip(header, values))
    close = parse_number(row.get("Close"))
    if close is None:
        raise ValueError("해외 종목 가격을 찾지 못했습니다. 미국 종목은 AAPL, TSLA처럼 입력하거나 거래소 접미사를 붙여 주세요.")
    previous_close = parse_number(row.get("Prev"))
    change = close - previous_close if previous_close is not None else None
    change_percent = (change / previous_close) * 100 if previous_close else None
    return {
        "inputSymbol": symbol,
        "symbol": row.get("Symbol") or stooq_symbol,
        "displaySymbol": re.sub(r"\.US$", "", row.get("Symbol") or stooq_symbol, flags=re.I),
        "name": row.get("Name") or configured(symbol).upper(),
        "exchange": (row.get("Symbol") or stooq_symbol).split(".")[1] if "." in (row.get("Symbol") or stooq_symbol) else "US",
        "currency": "USD",
        "price": close,
        "previousClose": previous_close,
        "change": change,
        "changePercent": change_percent,
        "open": parse_number(row.get("Open")),
        "high": parse_number(row.get("High")),
        "low": parse_number(row.get("Low")),
        "volume": parse_number(row.get("Volume")),
        "marketStatus": "DELAYED",
        "asOf": " ".join([row.get("Date") or "", row.get("Time") or ""]).strip(),
        "source": "Stooq",
    }


def fetch_quote(symbol: str) -> Dict[str, object]:
    return fetch_naver_quote(symbol) if stock_input_to_naver_code(symbol) else fetch_stooq_quote(symbol)


def stock_snapshot(symbol: str) -> Dict[str, object]:
    clean = configured(symbol)
    try:
        quote = fetch_quote(clean)
        return {"inputSymbol": clean, "quote": quote, "news": [], "error": ""}
    except Exception as error:
        return {"inputSymbol": clean, "quote": None, "news": [], "error": str(error) or "종목 정보를 가져오지 못했습니다."}


class DigitalTwinHandler(BaseHTTPRequestHandler):
    server_version = "DigitalTwinPython/0.1"

    def log_message(self, format, *args):
        if os.environ.get("WEB_SERVER_LOG_REQUESTS") == "1":
            super().log_message(format, *args)

    def do_OPTIONS(self):
        self.handle_request()

    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket()
            return
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def parsed(self):
        return urllib.parse.urlsplit(self.path)

    def parsed_query(self) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(self.parsed().query, keep_blank_values=True)

    def path_name(self) -> str:
        return urllib.parse.unquote(self.parsed().path or "/")

    def handle_websocket(self):
        if self.path_name() != "/ws":
            return self.send_payload(404, {"error": "웹소켓 엔드포인트를 찾지 못했습니다."})
        if not self.authorize_share():
            return
        key = configured(self.headers.get("Sec-WebSocket-Key"))
        if not key:
            return self.send_payload(400, {"error": "Sec-WebSocket-Key가 필요합니다."})
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", websocket_accept_key(key))
        self.end_headers()
        self.close_connection = True
        client = self.connection
        REALTIME_HUB.add(client)
        REALTIME_HUB.send(client, {
            "type": "realtime.connected",
            "payload": realtime_status_payload(),
            "occurredAt": now(),
        })
        try:
            while True:
                readable, _, _ = select.select([client], [], [], 25)
                if not readable:
                    if not REALTIME_HUB.send(client, {"type": "realtime.status", "payload": realtime_status_payload(), "occurredAt": now()}):
                        break
                    continue
                opcode, payload = read_websocket_frame(client)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    REALTIME_HUB.send(client, payload, opcode=0xA)
                    continue
                if opcode == 0x1 and payload.strip() == b"ping":
                    REALTIME_HUB.send(client, {"type": "realtime.pong", "payload": realtime_status_payload(), "occurredAt": now()})
        except (OSError, ValueError, socket.timeout):
            pass
        finally:
            REALTIME_HUB.remove(client)
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def read_json_body(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > MAX_BODY_BYTES:
            raise ValueError("요청이 너무 큽니다.")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def accepts_gzip(self) -> bool:
        return "gzip" in str(self.headers.get("Accept-Encoding") or "").lower()

    def send_payload(
        self,
        status: int,
        payload,
        content_type: str = "application/json; charset=utf-8",
        cors: bool = False,
        cache_control: str = "no-store",
    ):
        no_body = status in {204, 304} or self.command == "HEAD"
        body = b"" if no_body else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else (
                payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
            )
        )
        raw_length = len(body)
        compressed = False
        if not no_body and len(body) >= 1024 and self.accepts_gzip() and (content_type.startswith("application/json") or content_type.startswith("text/")):
            body = gzip.compress(body, compresslevel=6)
            compressed = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Vary", "Accept-Encoding")
            request_id = str(getattr(self, "_request_id", "") or "")
            if request_id:
                self.send_header("X-Request-ID", request_id)
            started_at = getattr(self, "_request_started_at", None)
            if started_at is not None:
                self.send_header("Server-Timing", "app;dur=" + ("%.1f" % ((time.monotonic() - started_at) * 1000.0)))
            if status in {429, 502, 503, 504}:
                retry_after = 5
                if isinstance(payload, dict):
                    retry_after = max(1, safe_int(payload.get("retryAfterSeconds"), 5, 1, 3600))
                self.send_header("Retry-After", str(retry_after))
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Response-Raw-Bytes", str(raw_length))
            self.send_header("X-Response-Wire-Bytes", str(len(body)))
            if cors:
                self.add_cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not no_body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        finally:
            started_at = getattr(self, "_request_started_at", None)
            duration_ms = (time.monotonic() - started_at) * 1000.0 if started_at is not None else 0.0
            API_PERFORMANCE.record(
                self.command,
                self.path_name(),
                status,
                duration_ms,
                raw_length,
                len(body),
                compressed,
            )

    def add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Authorization, Content-Type, Cache-Control, Pragma, X-Requested-With")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network")

    def send_redirect(self, location: str, cookie: str = ""):
        self.send_response(302)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def authorize_share(self) -> bool:
        if not share_mode_enabled():
            self._share_access = local_owner_access()
            return True
        if direct_loopback_request(self.client_address, self.headers):
            self._share_access = local_owner_access()
            return True
        parsed = self.parsed()
        query = self.parsed_query()
        supplied_owner = first_query(query, "owner_token")
        supplied_viewer = first_query(query, "share_token")
        supplied = supplied_owner or supplied_viewer
        requested_role = SHARE_ROLE_OWNER if supplied_owner else (SHARE_ROLE_VIEWER if supplied_viewer else "")
        access, matched_token = authenticate_share_token(supplied, requested_role)
        if access.authenticated:
            self._share_access = access
            clean_query = {key: values for key, values in query.items() if key not in {"share_token", "owner_token"}}
            encoded = urllib.parse.urlencode(clean_query, doseq=True)
            clean_path = (parsed.path or "/") + (("?" + encoded) if encoded else "")
            forwarded_proto = configured(self.headers.get("X-Forwarded-Proto")).lower()
            secure = forwarded_proto == "https" or configured(self.headers.get("CF-Visitor")).lower().find("https") >= 0
            self.send_redirect(
                clean_path,
                share_session_cookie(issue_share_session(access, matched_token), secure=secure),
            )
            return False
        access = share_access_from_cookie(self.headers.get("Cookie", ""))
        self._share_access = access
        if access.authenticated:
            return True
        if self.path_name().startswith("/api/"):
            self.send_payload(401, {"error": "공유 접근 토큰이 필요합니다."})
        else:
            self.send_payload(401, share_denied_page(), "text/html; charset=utf-8")
        return False

    def share_access(self) -> ShareAccess:
        return getattr(self, "_share_access", local_owner_access() if not share_mode_enabled() else anonymous_access())

    def handle_request(self):
        supplied_request_id = str(self.headers.get("X-Request-ID") or "").strip()
        self._request_id = supplied_request_id[:80] if re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", supplied_request_id) else uuid.uuid4().hex
        self._request_started_at = time.monotonic()
        if not self.authorize_share():
            return
        path = self.path_name()
        try:
            if (
                path.startswith("/api/")
                and self.command in {"POST", "PUT", "PATCH", "DELETE"}
                and not self.share_access().writable
            ):
                return self.send_payload(403, {"error": "조회 전용 링크에서는 데이터를 변경할 수 없습니다."})
            if path.startswith("/api/"):
                self.handle_api(path)
            else:
                self.serve_static(path)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except ValueError as error:
            self.send_payload(400, {
                "status": "error",
                "error": str(error) or "잘못된 요청입니다.",
                "retryable": False,
                "requestId": self._request_id,
            })
        except (urllib.error.URLError, TimeoutError, ExternalCircuitOpen, ExternalRateLimited) as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 502 " + path)
            self.send_payload(502, {
                "status": "unavailable",
                "error": str(error) or "외부 데이터 요청 실패",
                "retryable": True,
                "retryAfterSeconds": 5,
                "requestId": self._request_id,
                "dependencyStatus": {"externalApi": "unavailable"},
            })
        except Exception as error:
            report_runtime_error(operational_error_reporter(), "Python web server", error, "HTTP 500 " + path)
            self.send_payload(500, {
                "status": "error",
                "error": str(error) or "서버 오류",
                "retryable": False,
                "requestId": self._request_id,
            })

    def ensure_writable(self, message: str) -> bool:
        if not self.share_access().writable:
            self.send_payload(403, {"error": message})
            return False
        return True

    def handle_api(self, path: str):
        query = self.parsed_query()
        if path == "/api/share/access" and self.command == "GET":
            return self.send_payload(200, self.share_access().to_public_dict())
        if path == "/api/share/status" and self.command == "GET":
            return self.send_payload(200, share_runtime_status_payload(self.share_access()))
        if path == "/api/share/rotate" and self.command == "POST":
            if not self.ensure_writable("공유 보기 모드에서는 터널 주소를 갱신할 수 없습니다."):
                return
            body = self.read_json_body()
            payload = request_share_tunnel_rotation(
                reason=str(body.get("reason") or "manual"),
                requested_by=str(self.share_access().role or "local-owner"),
            )
            return self.send_payload(202, payload)
        if path == "/api/version" and self.command == "GET":
            return self.send_payload(200, {
                **runtime_identity(),
                "startedAt": WEB_PROCESS_STARTED_AT,
            })

        if path == "/api/operations/performance" and self.command == "GET":
            return self.send_payload(200, API_PERFORMANCE.snapshot(), cache_control="no-store")

        if path == "/api/service-accounts":
            if self.command == "GET":
                return self.send_payload(200, service_accounts_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_account_payload(self.read_json_body()))

        account_watchlist_match = re.match(r"^/api/service-accounts/([^/]+)/watchlist(?:/([^/]+))?$", path)
        if account_watchlist_match:
            account_id = urllib.parse.unquote(account_watchlist_match.group(1))
            symbol = urllib.parse.unquote(account_watchlist_match.group(2) or "")
            if self.command == "GET" and not symbol:
                return self.send_payload(200, account_watchlist_payload(account_id))
            if self.command == "POST" and not symbol:
                if not self.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, add_account_watchlist_payload(account_id, self.read_json_body()))
            if self.command == "PUT" and not symbol:
                if not self.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, replace_account_watchlist_payload(account_id, self.read_json_body()))
            if self.command == "DELETE" and symbol:
                if not self.ensure_writable("공유 모드에서는 관심 종목을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, remove_account_watchlist_payload(account_id, symbol))

        account_match = re.match(r"^/api/service-accounts/([^/]+)$", path)
        if account_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 계정 DB를 변경할 수 없습니다."):
                return
            return self.send_payload(200, remove_account_payload(urllib.parse.unquote(account_match.group(1))))

        if path == "/api/settings":
            if self.command == "GET":
                return self.send_payload(200, settings_status_payload(self.share_access()))
            if self.command == "PUT":
                if not self.ensure_writable("공유 모드에서는 서버 설정을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_settings_payload(self.read_json_body(), self.share_access()))

        if path == "/api/time-series-platform/status" and self.command == "GET":
            return self.send_payload(200, time_series_platform_status_payload())

        if path == "/api/reasoning-engine/status" and self.command == "GET":
            return self.send_payload(200, reasoning_engine_platform_status_payload(query))

        if path == "/api/reasoning-engine/comparisons" and self.command == "GET":
            return self.send_payload(200, reasoning_engine_comparisons_payload(query))

        if path == "/api/investment-reasoning/cases" and self.command == "GET":
            return self.send_payload(200, investment_reasoning_cases_payload(query))

        if path == "/api/ontology/rulebox":
            if self.command == "GET":
                return self.send_payload(200, ontology_rulebox_payload(
                    force=request_bool(first_query(query, "refresh"), False),
                ))
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 TypeDB RuleBox를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_ontology_rulebox_payload(self.read_json_body()))

        ontology_catalog_match = re.match(
            r"^/api/ontology/catalog/(summary|classes|relations|rules|hypotheses|inferences|lineage)$",
            path,
        )
        if ontology_catalog_match and self.command == "GET":
            return self.send_payload(200, ontology_catalog_api_payload(
                ontology_catalog_match.group(1),
                query,
            ))

        if path == "/api/ontology/language":
            if self.command == "GET":
                return self.send_payload(200, ontology_language_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 보편언어 사전을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/validate" and self.command == "POST":
            return self.send_payload(200, validate_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/preview" and self.command == "POST":
            return self.send_payload(200, preview_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/language/suggest" and self.command == "POST":
            return self.send_payload(200, suggest_ontology_language_payload(self.read_json_body()))

        if path == "/api/ontology/rulebox/run" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 TypeDB 네이티브 규칙 추론을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_rulebox_payload(self.read_json_body()))

        if path == "/api/ontology/diagnostics" and self.command == "GET":
            return self.send_payload(200, ontology_diagnostics_payload(query))

        if path == "/api/ontology/inference-ledger" and self.command == "GET":
            payload = ontology_inference_ledger_api_payload(query)
            status = 503 if request_bool(first_query(query, "direct"), False) and not payload.get("usable") else 200
            return self.send_payload(status, payload)

        if path == "/api/ontology/audit" and self.command == "GET":
            return self.send_payload(200, ontology_audit_payload(query))

        ontology_audit_match = re.match(r"^/api/ontology/audit/([^/]+)$", path)
        if ontology_audit_match and self.command == "GET":
            return self.send_payload(200, ontology_audit_payload(
                query,
                urllib.parse.unquote(ontology_audit_match.group(1)),
            ))

        if path == "/api/ontology/rulebox/candidates" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 TypeDB RuleBox 후보를 생성할 수 없습니다."):
                return
            return self.send_payload(200, propose_ontology_rule_candidates_payload(self.read_json_body()))

        if path == "/api/ontology/seed" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 그래프 시드를 실행할 수 없습니다."):
                return
            return self.send_payload(200, seed_ontology_payload(self.read_json_body()))

        if path == "/api/ontology/experiments" and self.command == "GET":
            return self.send_payload(200, list_ontology_experiments_payload(query))

        if path == "/api/ontology/experiments" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 생성할 수 없습니다."):
                return
            return self.send_payload(200, create_ontology_experiment_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/status" and self.command == "GET":
            return self.send_payload(200, ontology_experiments_status_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/ontology/reasoning/status" and self.command == "GET":
            return self.send_payload(200, ontology_reasoning_status_payload())

        if path == "/api/ontology/experiments/once" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_experiments_once_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/suggest" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 AI 온톨로지 실험 제안을 생성할 수 없습니다."):
                return
            return self.send_payload(200, suggest_ontology_experiments_payload(self.read_json_body()))

        if path == "/api/ontology/experiments/apply" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return self.send_payload(200, apply_ontology_experiments_batch_payload(self.read_json_body()))

        ontology_experiment_run_match = re.match(r"^/api/ontology/experiments/([^/]+)/run$", path)
        if ontology_experiment_run_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험을 실행할 수 없습니다."):
                return
            return self.send_payload(200, run_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_run_match.group(1)),
                self.read_json_body(),
            ))

        ontology_experiment_apply_match = re.match(r"^/api/ontology/experiments/([^/]+)/apply$", path)
        if ontology_experiment_apply_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 제안을 운영 반영할 수 없습니다."):
                return
            return self.send_payload(200, apply_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_apply_match.group(1)),
                self.read_json_body(),
            ))

        ontology_experiment_activate_match = re.match(r"^/api/ontology/experiments/([^/]+)/activate$", path)
        if ontology_experiment_activate_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return self.send_payload(200, activate_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_activate_match.group(1)),
            ))

        ontology_experiment_pause_match = re.match(r"^/api/ontology/experiments/([^/]+)/pause$", path)
        if ontology_experiment_pause_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 온톨로지 실험 상태를 변경할 수 없습니다."):
                return
            return self.send_payload(200, pause_ontology_experiment_payload(
                urllib.parse.unquote(ontology_experiment_pause_match.group(1)),
            ))

        ontology_experiment_match = re.match(r"^/api/ontology/experiments/([^/]+)$", path)
        if ontology_experiment_match and self.command == "GET":
            return self.send_payload(200, ontology_experiment_payload(urllib.parse.unquote(ontology_experiment_match.group(1))))

        if path == "/api/investment-strategy-proposals" and self.command == "GET":
            return self.send_payload(200, list_investment_strategy_proposals_payload(query))

        if path == "/api/investment-strategy-proposals/status" and self.command == "GET":
            return self.send_payload(200, investment_strategy_proposals_status_payload())

        strategy_proposal_action_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)/(validate|approve|performance)$", path)
        if strategy_proposal_action_match:
            proposal_id = urllib.parse.unquote(strategy_proposal_action_match.group(1))
            action = strategy_proposal_action_match.group(2)
            if action == "performance" and self.command == "GET":
                return self.send_payload(200, investment_strategy_proposal_performance_payload(proposal_id))
            if self.command == "POST":
                if action == "validate":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 제안을 검증할 수 없습니다."):
                        return
                    return self.send_payload(200, validate_investment_strategy_proposal_payload(proposal_id, self.read_json_body()))
                if action == "approve":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 제안을 승인할 수 없습니다."):
                        return
                    return self.send_payload(200, approve_investment_strategy_proposal_payload(proposal_id, self.read_json_body()))
                if action == "performance":
                    if not self.ensure_writable("공유 모드에서는 투자 전략 성과를 기록할 수 없습니다."):
                        return
                    return self.send_payload(200, record_investment_strategy_proposal_performance_payload(proposal_id, self.read_json_body()))

        strategy_proposal_match = re.match(r"^/api/investment-strategy-proposals/([^/]+)$", path)
        if strategy_proposal_match and self.command == "GET":
            return self.send_payload(200, investment_strategy_proposal_payload(urllib.parse.unquote(strategy_proposal_match.group(1))))

        if path == "/api/symbol-universe":
            if self.command == "GET":
                return self.send_payload(200, symbol_universe_payload(query))

        if path == "/api/symbol-universe/suggest":
            if self.command == "GET":
                return self.send_payload(200, symbol_universe_suggest_payload(query))

        if path == "/api/symbol-universe/refresh/status" and self.command == "GET":
            return self.send_payload(200, symbol_universe_refresh_status(first_query(query, "jobId")))

        if path == "/api/symbol-universe/refresh" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 종목 유니버스를 갱신할 수 없습니다."):
                return
            return self.send_payload(202, request_symbol_universe_refresh(self.read_json_body()))

        if path == "/api/notification-templates":
            if self.command == "GET":
                return self.send_payload(200, list_templates_payload())
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_template_payload(self.read_json_body()))

        if path == "/api/notification-rules":
            if self.command == "GET":
                return self.send_payload(200, list_notification_rules_payload(include_internal_notification_query(query)))
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_notification_rule_payload(self.read_json_body()))

        if path == "/api/notification-jobs" and self.command == "GET":
            return self.send_payload(200, notification_jobs_payload(query))

        if path == "/api/notification-jobs/read-all" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 알림 확인 상태를 변경할 수 없습니다."):
                return
            return self.send_payload(200, mark_all_notifications_read_payload(self.read_json_body()))

        notification_receipt_match = re.match(r"^/api/notification-jobs/([^/]+)/receipt$", path)
        if notification_receipt_match and self.command in {"POST", "PUT", "PATCH"}:
            if not self.ensure_writable("공유 모드에서는 알림 확인 상태를 변경할 수 없습니다."):
                return
            job_id = urllib.parse.unquote(notification_receipt_match.group(1))
            payload = update_notification_receipt_payload(job_id, self.read_json_body())
            return self.send_payload(200 if not payload.get("error") else 404, payload)

        notification_section_match = re.match(
            r"^/api/notification-jobs/([^/]+)/(reasoning|ai-review|delivery)$",
            path,
        )
        if notification_section_match and self.command == "GET":
            payload = notification_job_detail_payload(
                urllib.parse.unquote(notification_section_match.group(1)),
                first_query(query, "recipientId") or "local-owner",
                section=notification_section_match.group(2),
                include_sensitive=not self.share_access().shared,
            )
            return self.send_payload(200 if payload.get("jobId") else 404, payload or {"error": "알림 작업을 찾지 못했습니다."})

        notification_job_match = re.match(r"^/api/notification-jobs/([^/]+)$", path)
        if notification_job_match and self.command == "GET":
            payload = notification_job_detail_payload(
                urllib.parse.unquote(notification_job_match.group(1)),
                first_query(query, "recipientId") or "local-owner",
                include_sensitive=not self.share_access().shared,
            )
            return self.send_payload(200 if payload.get("job") else 404, payload or {"error": "알림 작업을 찾지 못했습니다."})

        if path == "/api/notification-jobs/replay" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 알림을 재발송할 수 없습니다."):
                return
            return self.send_payload(200, replay_notification_payload(self.read_json_body()))

        if path == "/api/research-evidence" and self.command == "GET":
            return self.send_payload(200, research_evidence_payload(query))

        if path == "/api/research-evidence/revalidate" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 근거 검증 상태를 갱신할 수 없습니다."):
                return
            return self.send_payload(200, revalidate_research_evidence_payload(self.read_json_body()))

        research_evidence_match = re.match(r"^/api/research-evidence/([^/]+)$", path)
        if research_evidence_match and self.command == "GET":
            payload = research_evidence_detail_payload(urllib.parse.unquote(research_evidence_match.group(1)))
            return self.send_payload(200 if payload.get("item") else 404, payload or {"error": "리서치 근거를 찾지 못했습니다."})

        if path == "/api/investment-calendar/events":
            if self.command == "GET":
                return self.send_payload(200, investment_calendar_payload(query))
            if self.command in {"POST", "PUT"}:
                if not self.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, save_investment_calendar_event_payload(self.read_json_body()))

        if path == "/api/investment-calendar/candidates" and self.command == "GET":
            return self.send_payload(200, investment_calendar_candidates_payload(query))

        if path == "/api/investment-calendar/candidates/research" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 AI 리서치 캘린더 후보를 생성할 수 없습니다."):
                return
            return self.send_payload(200, research_investment_calendar_candidates_payload(self.read_json_body()))

        if path == "/api/investment-calendar/discovery" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 투자 일정 탐색을 실행할 수 없습니다."):
                return
            return self.send_payload(200, discover_investment_calendar_payload(self.read_json_body()))

        calendar_candidate_match = re.match(r"^/api/investment-calendar/candidates/([^/]+)/(approve|reject)$", path)
        if calendar_candidate_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 투자 캘린더 후보를 검토할 수 없습니다."):
                return
            candidate_id = urllib.parse.unquote(calendar_candidate_match.group(1))
            action = calendar_candidate_match.group(2)
            if action == "approve":
                return self.send_payload(200, approve_investment_calendar_candidate_payload(candidate_id, self.read_json_body()))
            return self.send_payload(200, reject_investment_calendar_candidate_payload(candidate_id, self.read_json_body()))

        if path == "/api/investment-calendar/reminders/run" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 투자 캘린더 알림을 큐잉할 수 없습니다."):
                return
            return self.send_payload(200, investment_calendar_reminders_once_payload())

        if path == "/api/investment-calendar/sync-official" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 공식 투자 일정을 동기화할 수 없습니다."):
                return
            return self.send_payload(200, investment_calendar_sync_official_payload())

        calendar_event_match = re.match(r"^/api/investment-calendar/events/([^/]+)$", path)
        if calendar_event_match:
            event_id = urllib.parse.unquote(calendar_event_match.group(1))
            if self.command == "GET":
                payload = investment_calendar_payload({"limit": ["500"]})
                payload["event"] = next((item for item in payload.get("events") or [] if item.get("eventId") == event_id), None)
                return self.send_payload(200 if payload.get("event") else 404, payload if payload.get("event") else {"error": "투자 캘린더 이벤트를 찾지 못했습니다."})
            if self.command == "DELETE":
                if not self.ensure_writable("공유 모드에서는 투자 캘린더 이벤트를 변경할 수 없습니다."):
                    return
                return self.send_payload(200, delete_investment_calendar_event_payload(event_id))

        evidence_match = re.match(r"^/api/research-evidence/([^/]+)$", path)
        if evidence_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 저장된 리서치 근거를 변경할 수 없습니다."):
                return
            evidence_id = urllib.parse.unquote(evidence_match.group(1))
            return self.send_payload(200, delete_research_evidence_payload(evidence_id, query))

        if path == "/api/notification-schedules" and self.command == "GET":
            return self.send_payload(200, notification_schedules_payload(include_internal_notification_query(query)))

        if path == "/api/notification-templates/test-send" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 실제 알림을 발송할 수 없습니다."):
                return
            status, payload = notification_template_test_payload(self.read_json_body())
            return self.send_payload(status, payload)

        template_match = re.match(r"^/api/notification-templates/([^/]+)$", path)
        if template_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                return
            return self.send_payload(200, reset_template_payload(urllib.parse.unquote(template_match.group(1))))

        rule_match = re.match(r"^/api/notification-rules/([^/]+)$", path)
        if rule_match and self.command == "DELETE":
            if not self.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                return
            return self.send_payload(200, reset_notification_rule_payload(urllib.parse.unquote(rule_match.group(1))))

        if path == "/api/data-api/fred/observations":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, fetch_json_url(normalize_fred_observations_url(query)), cors=True)

        if path == "/api/data-api/opendart/company":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, fetch_json_url(normalize_opendart_company_url(query)), cors=True)

        if path == "/api/mock-market/scenarios":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            return self.send_payload(200, mock_market_scenario_list(), cors=True)

        if path == "/api/mock-market/candles":
            if self.command == "OPTIONS":
                return self.send_payload(204, {}, cors=True)
            flat_query = {key: first_query(query, key) for key in query}
            return self.send_payload(200, mock_market_payload(flat_query), cors=True)

        if path == "/api/flow-lens" and self.command == "GET":
            return self.send_payload(200, flow_lens_read_payload(query), cache_control="no-store")

        if path == "/api/capital-flow/summary" and self.command == "GET":
            return self.send_payload(200, capital_flow_api_payload(query), cache_control="no-store")

        if path == "/api/capital-flow/quality" and self.command == "GET":
            return self.send_payload(200, capital_flow_api_payload(query, quality_only=True), cache_control="no-store")

        if path == "/api/capital-flow/portfolio" and self.command == "GET":
            return self.send_payload(
                200,
                capital_flow_api_payload(query, snapshot=persisted_flow_lens_snapshot()),
                cache_control="no-store",
            )

        capital_flow_subject_match = re.match(r"^/api/capital-flow/subjects/([^/]+)$", path)
        if capital_flow_subject_match and self.command == "GET":
            subject_id = urllib.parse.unquote(capital_flow_subject_match.group(1))
            return self.send_payload(
                200,
                capital_flow_api_payload(query, subject_id=subject_id),
                cache_control="no-store",
            )

        if path == "/api/dashboard/summary" and self.command == "GET":
            return self.send_payload(200, console_dashboard_api_payload(query), cache_control="no-store")

        portfolio_console_views = {
            "/api/portfolio/summary": "summary",
            "/api/portfolio/positions": "positions",
            "/api/portfolio/rebalance": "rebalance",
            "/api/portfolio/activity": "activity",
            "/api/portfolio/interpretation": "interpretation",
        }
        if path in portfolio_console_views and self.command == "GET":
            return self.send_payload(
                200,
                console_portfolio_api_payload(query, portfolio_console_views[path]),
                cache_control="no-store",
            )

        if path == "/api/market/instruments" and self.command == "GET":
            return self.send_payload(200, console_market_instruments_api_payload(query), cache_control="no-store")

        if path == "/api/market/evidence" and self.command == "GET":
            return self.send_payload(200, console_market_evidence_api_payload(query), cache_control="no-store")

        if path == "/api/decisions" and self.command == "GET":
            return self.send_payload(200, console_decisions_api_payload(query), cache_control="no-store")

        console_decision_match = re.match(r"^/api/decisions/([^/]+)$", path)
        if console_decision_match and self.command == "GET":
            case_id = urllib.parse.unquote(console_decision_match.group(1))
            payload = investment_case_api_payload(query, case_id=case_id)
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        if path == "/api/operations/health" and self.command == "GET":
            return self.send_payload(200, console_operations_health_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")

        if path == "/api/investment-model" and self.command == "GET":
            return self.send_payload(200, investment_model_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")

        if path == "/api/investment-cases" and self.command == "GET":
            return self.send_payload(200, investment_case_api_payload(query), cache_control="no-store")

        investment_case_section_match = re.match(
            r"^/api/investment-cases/([^/]+)/(history|trace)$",
            path,
        )
        if investment_case_section_match and self.command == "GET":
            case_id = urllib.parse.unquote(investment_case_section_match.group(1))
            payload = investment_case_api_payload(
                query,
                case_id=case_id,
                section=investment_case_section_match.group(2),
            )
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        investment_case_match = re.match(r"^/api/investment-cases/([^/]+)$", path)
        if investment_case_match and self.command == "GET":
            case_id = urllib.parse.unquote(investment_case_match.group(1))
            payload = investment_case_api_payload(query, case_id=case_id)
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        if path in {"/api/investment-flow", "/api/investment-validation"} and self.command == "GET":
            payload = investment_flow_api_payload(query)
            payload["view"] = "validation" if path.endswith("validation") else "flow"
            return self.send_payload(200, payload, cache_control="no-store")

        investment_flow_match = re.match(r"^/api/investment-flow/([^/]+)$", path)
        if investment_flow_match and self.command == "GET":
            episode_id = urllib.parse.unquote(investment_flow_match.group(1))
            payload = investment_flow_api_payload(query, episode_id=episode_id)
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload, cache_control="no-store")

        if path == "/api/investment-analysis" and self.command == "GET":
            mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
            return self.send_payload(200, investment_analysis_snapshot(
                mock=mock_value in {"1", "true", "mock"},
                watchlist_symbols=first_query(query, "watchlistSymbols"),
            ))

        if path == "/api/bootstrap" and self.command == "GET":
            return self.send_payload(200, snapshot_payload())

        if path == "/api/realtime/status" and self.command == "GET":
            return self.send_payload(200, realtime_status_payload())

        if path == "/api/external-data/status" and self.command == "GET":
            return self.send_payload(200, external_data_status_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")

        if path == "/api/profile" and self.command == "PUT":
            body = self.read_json_body()
            if not body.get("ownerName") or not body.get("assistantName"):
                return self.send_payload(400, {"error": "이름과 비서 이름은 필요합니다."})
            store = save_store(lambda draft: draft.update({"profile": {**draft["profile"], **body}}))
            new_domain_event(
                APP_PROFILE_UPDATED,
                "profile",
                {
                    "ownerName": store["profile"].get("ownerName"),
                    "assistantName": store["profile"].get("assistantName"),
                },
            )
            return self.send_payload(200, {"profile": store["profile"]})

        if path == "/api/chat" and self.command == "POST":
            if self.share_access().shared:
                return self.send_payload(403, {"error": "로컬 AI 실행은 이 컴퓨터에서 직접 접속할 때만 사용할 수 있습니다."})
            return self.send_payload(200, chat_payload(self.read_json_body()))

        if path == "/api/investment-brain/questions" and self.command == "POST":
            if self.share_access().shared:
                return self.send_payload(403, {"error": "투자 브레인 질의는 이 컴퓨터에서 직접 접속할 때만 사용할 수 있습니다."})
            return self.send_payload(200, investment_brain_question_payload(self.read_json_body()))

        instrument_timeline_match = re.match(r"^/api/instruments/([^/]+)/timeline$", path)
        if instrument_timeline_match and self.command == "GET":
            try:
                payload = build_instrument_timeline_query_service(operational_read_settings()).query(
                    InstrumentTimelineQuery(
                        symbol=urllib.parse.unquote(instrument_timeline_match.group(1)),
                        account_id=first_query(query, "accountId"),
                        range_key=first_query(query, "range") or "3m",
                        interval=first_query(query, "interval"),
                    )
                )
            except ValueError as error:
                return self.send_payload(400, {"error": str(error)})
            return self.send_payload(200, payload, cache_control="no-store")

        if path == "/api/investment-brain/episodes" and self.command == "GET":
            return self.send_payload(200, investment_brain_episodes_api_payload(query))

        episode_detail_match = re.match(r"^/api/investment-brain/episodes/([^/]+)$", path)
        if episode_detail_match and self.command == "GET":
            payload = build_investment_brain_service().episode_detail(
                urllib.parse.unquote(episode_detail_match.group(1)),
            )
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload)

        if path == "/api/portfolio-lifecycle" and self.command == "GET":
            return self.send_payload(200, portfolio_lifecycle_payload(query), cache_control="no-store")

        action_plan_match = re.match(r"^/api/action-plans/([^/]+)/(approve|reject|execute)$", path)
        if action_plan_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 실행계획을 검토하거나 제출할 수 없습니다."):
                return
            plan_id = urllib.parse.unquote(action_plan_match.group(1))
            action = action_plan_match.group(2)
            if action == "execute":
                payload = execute_action_plan_payload(plan_id)
            else:
                payload = review_action_plan_payload(
                    plan_id,
                    "approved" if action == "approve" else "rejected",
                    self.read_json_body(),
                )
            return self.send_payload(200 if payload.get("status") != "error" else 400, payload)

        action_plan_fills_match = re.match(r"^/api/action-plans/([^/]+)/fills$", path)
        if action_plan_fills_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 실제 체결을 기록할 수 없습니다."):
                return
            payload = record_action_plan_fills_payload(
                urllib.parse.unquote(action_plan_fills_match.group(1)),
                self.read_json_body(),
            )
            return self.send_payload(200 if payload.get("status") != "error" else 400, payload)

        if path == "/api/investment-brain/performance" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 500)
            except ValueError:
                limit = 500
            return self.send_payload(200, build_investment_brain_service().performance(
                account_id=first_query(query, "accountId"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/hypothesis-templates" and self.command == "GET":
            return self.send_payload(200, hypothesis_templates_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/investment-brain/hypothesis-lifecycles" and self.command == "GET":
            return self.send_payload(200, investment_brain_hypothesis_lifecycles_api_payload(query))

        if path == "/api/investment-brain/hypotheses" and self.command == "GET":
            return self.send_payload(200, investment_brain_hypothesis_workspace_api_payload(query))

        hypothesis_detail_match = re.match(r"^/api/investment-brain/hypotheses/([^/]+)$", path)
        if hypothesis_detail_match and self.command == "GET":
            return self.send_payload(200, build_investment_brain_service(operational_read_settings()).hypothesis_workspace_detail(
                urllib.parse.unquote(hypothesis_detail_match.group(1)),
            ))

        if path == "/api/investment-brain/hypothesis-policy-versions" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 40)
            except ValueError:
                limit = 40
            return self.send_payload(200, hypothesis_policy_versions_api_payload(
                limit=limit,
                force=request_bool(first_query(query, "refresh"), False),
            ))

        if path == "/api/investment-brain/hypothesis-policy-versions/baseline" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 RuleBox 기준선 버전을 기록할 수 없습니다."):
                return
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().record_hypothesis_policy_baseline(
                author=configured(body.get("author")) or "web-main",
            ))

        replay_job_match = re.match(r"^/api/investment-brain/replay-jobs/([^/]+)$", path)
        if replay_job_match and self.command == "GET":
            replay_service = build_historical_replay_job_service(
                operational_read_settings(), execution_enabled=False,
            )
            job = replay_service.get(urllib.parse.unquote(replay_job_match.group(1)))
            return self.send_payload(200 if job else 404, {
                "status": str(job.get("status") or "not-found") if job else "not-found",
                "job": job,
            })

        if path == "/api/investment-brain/hypothesis-replay" and self.command in {"GET", "POST"}:
            replay_service = build_historical_replay_job_service(
                operational_read_settings(), execution_enabled=False,
            )
            if self.command == "GET":
                return self.send_payload(200, replay_service.list(replay_kind="hypothesis", limit=20))
            if not self.ensure_writable("공유 보기 모드에서는 과거 가설 재현 작업을 시작할 수 없습니다."):
                return
            body = self.read_json_body()
            try:
                limit = int(body.get("limit") or 500)
            except (TypeError, ValueError):
                limit = 500
            job = replay_service.enqueue("hypothesis", {
                "accountId": configured(body.get("accountId")),
                "symbol": configured(body.get("symbol")),
                "limit": max(1, min(2000, limit)),
            })
            return self.send_payload(202, {"status": "queued", "job": job})

        if path == "/api/investment-brain/decision-replay" and self.command in {"GET", "POST"}:
            replay_service = build_historical_replay_job_service(
                operational_read_settings(), execution_enabled=False,
            )
            if self.command == "GET":
                return self.send_payload(200, replay_service.list(replay_kind="decision", limit=20))
            if not self.ensure_writable("공유 보기 모드에서는 과거 판단 재현 작업을 시작할 수 없습니다."):
                return
            body = self.read_json_body()
            try:
                limit = int(body.get("limit") or 500)
            except (TypeError, ValueError):
                limit = 500
            try:
                case_limit = int(body.get("caseLimit") or 30)
            except (TypeError, ValueError):
                case_limit = 30
            include_cases_value = body.get("includeCases")
            include_cases = str(include_cases_value or "").strip().lower() in {"1", "true", "yes", "on"}
            job = replay_service.enqueue("decision", {
                "accountId": str(body.get("accountId") or ""),
                "symbol": str(body.get("symbol") or ""),
                "limit": max(1, min(2000, limit)),
                "includeCases": include_cases,
                "caseLimit": max(1, min(100, case_limit)),
                "replayMode": str(body.get("replayMode") or "strict-replay"),
            })
            return self.send_payload(202, {"status": "queued", "job": job})

        if path == "/api/investment-brain/hypothesis-quality-review" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 가설 품질 검토 제안을 저장할 수 없습니다."):
                return
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().review_hypothesis_quality(
                account_id=configured(body.get("accountId")),
                symbol=configured(body.get("symbol")),
                market_id=configured(body.get("marketId")),
                scope=configured(body.get("scope")),
                reviewed_by=configured(body.get("reviewedBy")) or "web-main",
            ))

        lifecycle_policy_preview_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)/preview$", path)
        if lifecycle_policy_preview_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 가설 수명주기 정책을 미리보기할 수 없습니다."):
                return
            body = self.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            return self.send_payload(200, build_investment_brain_service().preview_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_preview_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        lifecycle_policy_approve_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)/approve$", path)
        if lifecycle_policy_approve_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 가설 수명주기 정책을 승인할 수 없습니다."):
                return
            body = self.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            return self.send_payload(200, build_investment_brain_service().approve_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_approve_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        policy_version_restore_match = re.match(r"^/api/investment-brain/hypothesis-policy-versions/([^/]+)/restore$", path)
        if policy_version_restore_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 RuleBox 버전을 복원할 수 없습니다."):
                return
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().restore_hypothesis_policy_version(
                urllib.parse.unquote(policy_version_restore_match.group(1)),
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        lifecycle_policy_match = re.match(r"^/api/investment-brain/hypothesis-policies/([^/]+)$", path)
        if lifecycle_policy_match and self.command == "PATCH":
            if not self.ensure_writable("공유 모드에서는 가설 수명주기 정책을 변경할 수 없습니다."):
                return
            body = self.read_json_body()
            policy = body.get("policy") if isinstance(body.get("policy"), dict) else body
            # Backward-compatible route: retain the endpoint but remove the
            # old direct-write bypass from the web surface.
            return self.send_payload(200, build_investment_brain_service().approve_hypothesis_lifecycle_policy(
                urllib.parse.unquote(lifecycle_policy_match.group(1)),
                policy,
                configured(body.get("changeReason")),
                author=configured(body.get("author")) or "web-main",
                symbols=body.get("symbols") or body.get("symbol"),
                world_id=configured(body.get("worldId")),
            ))

        if path == "/api/investment-brain/research-runs" and self.command == "GET":
            return self.send_payload(200, investment_brain_research_runs_api_payload(query))

        research_run_detail_match = re.match(r"^/api/investment-brain/research-runs/([^/]+)$", path)
        if research_run_detail_match and self.command == "GET":
            payload = build_investment_brain_service().research_run_detail(
                urllib.parse.unquote(research_run_detail_match.group(1)),
            )
            return self.send_payload(200 if payload.get("status") == "ok" else 404, payload)

        if path == "/api/investment-brain/hypothesis-proposals" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().hypothesis_proposals(
                status=first_query(query, "status"),
                symbol=first_query(query, "symbol"),
                limit=limit,
            ))

        if path == "/api/investment-brain/hypothesis-development" and self.command == "GET":
            return self.send_payload(200, hypothesis_development_cases_payload(query))

        if path == "/api/investment-brain/hypothesis-development/process" and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 가설 자동 검증을 실행할 수 없습니다."):
                return
            return self.send_payload(200, process_hypothesis_development_payload(self.read_json_body()))

        hypothesis_development_approve_match = re.match(r"^/api/investment-brain/hypothesis-development/([^/]+)/approve$", path)
        if hypothesis_development_approve_match and self.command == "POST":
            if not self.ensure_writable("공유 모드에서는 검증된 가설을 운영 반영할 수 없습니다."):
                return
            return self.send_payload(200, approve_hypothesis_development_payload(
                urllib.parse.unquote(hypothesis_development_approve_match.group(1)),
                self.read_json_body(),
            ))

        hypothesis_development_match = re.match(r"^/api/investment-brain/hypothesis-development/([^/]+)$", path)
        if hypothesis_development_match and self.command == "GET":
            return self.send_payload(200, hypothesis_development_case_payload(
                urllib.parse.unquote(hypothesis_development_match.group(1)),
            ))

        hypothesis_proposal_match = re.match(r"^/api/investment-brain/hypothesis-proposals/([^/]+)$", path)
        if hypothesis_proposal_match and self.command == "PATCH":
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().review_hypothesis_proposal(
                hypothesis_proposal_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))

        if path == "/api/investment-brain/learning-proposals" and self.command == "GET":
            try:
                limit = int(first_query(query, "limit") or 50)
            except ValueError:
                limit = 50
            return self.send_payload(200, build_investment_brain_service().learning_proposals(
                status=first_query(query, "status"),
                limit=limit,
            ))

        learning_match = re.match(r"^/api/investment-brain/learning-proposals/([^/]+)$", path)
        if learning_match and self.command == "PATCH":
            body = self.read_json_body()
            return self.send_payload(200, build_investment_brain_service().review_learning_proposal(
                learning_match.group(1),
                configured(body.get("status")),
                configured(body.get("note")),
            ))

        if path == "/api/memories":
            if self.command == "GET":
                return self.send_payload(200, {"memories": read_store()["memories"]})
            if self.command == "POST":
                body = self.read_json_body()
                content = configured(body.get("content"))
                if not content:
                    return self.send_payload(400, {"error": "기억 내용을 입력하세요."})
                stamped = now()
                memory = {
                    "id": new_id("mem"),
                    "content": content,
                    "category": body.get("category") if body.get("category") in MEMORY_CATEGORIES else "other",
                    "status": "candidate" if body.get("status") == "candidate" else "approved",
                    "importance": max(1, min(5, int(body.get("importance") or 3))),
                    "source": "manual",
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = save_store(lambda draft: draft["memories"].insert(0, memory))
                new_domain_event(
                    APP_MEMORY_RECORDED,
                    memory["id"],
                    {"memoryId": memory["id"], "category": memory["category"], "source": "manual"},
                )
                return self.send_payload(200, {"memory": memory, "memories": store["memories"]})

        memory_match = re.match(r"^/api/memories/([^/]+)$", path)
        if memory_match and self.command == "PATCH":
            memory_id = memory_match.group(1)
            body = self.read_json_body()

            def mutate(draft):
                next_memories = []
                for memory in draft["memories"]:
                    if memory.get("id") == memory_id:
                        updated = {**memory, **body, "updatedAt": now()}
                        if body.get("content"):
                            updated["content"] = configured(body.get("content"))
                        next_memories.append(updated)
                    else:
                        next_memories.append(memory)
                draft["memories"] = next_memories

            store = save_store(mutate)
            new_domain_event(APP_MEMORY_UPDATED, memory_id, {"memoryId": memory_id})
            return self.send_payload(200, {"memories": store["memories"]})
        if memory_match and self.command == "DELETE":
            memory_id = memory_match.group(1)
            store = save_store(lambda draft: draft.update({"memories": [memory for memory in draft["memories"] if memory.get("id") != memory_id]}))
            new_domain_event(APP_MEMORY_REMOVED, memory_id, {"memoryId": memory_id})
            return self.send_payload(200, {"memories": store["memories"]})

        if path == "/api/items":
            if self.command == "GET":
                return self.send_payload(200, {"items": read_store()["items"]})
            if self.command == "POST":
                body = self.read_json_body()
                title = configured(body.get("title"))
                if body.get("type") not in DOMAIN_TYPES or not title:
                    return self.send_payload(400, {"error": "유형과 제목을 입력하세요."})
                stamped = now()
                item = {
                    "id": new_id("item"),
                    "type": body.get("type"),
                    "title": title,
                    "status": configured(body.get("status")) or "open",
                    "date": configured(body.get("date")),
                    "amount": normalize_amount(body.get("amount")),
                    "currency": configured(body.get("currency")),
                    "ticker": configured(body.get("ticker")).upper(),
                    "location": configured(body.get("location")),
                    "notes": configured(body.get("notes")),
                    "fields": normalize_item_fields(body.get("fields")),
                    "createdAt": stamped,
                    "updatedAt": stamped,
                }
                store = save_store(lambda draft: draft["items"].insert(0, item))
                new_domain_event(
                    APP_ITEM_UPDATED,
                    item["id"],
                    {"itemId": item["id"], "type": item["type"], "status": item["status"]},
                )
                return self.send_payload(200, {"item": item, "items": store["items"]})

        item_match = re.match(r"^/api/items/([^/]+)$", path)
        if item_match and self.command == "PATCH":
            item_id = item_match.group(1)
            body = self.read_json_body()
            store = save_store(lambda draft: draft.update({"items": [patch_item(item, body) if item.get("id") == item_id else item for item in draft["items"]]}))
            new_domain_event(APP_ITEM_UPDATED, item_id, {"itemId": item_id, "patched": True})
            return self.send_payload(200, {"items": store["items"]})
        if item_match and self.command == "DELETE":
            item_id = item_match.group(1)
            store = save_store(lambda draft: draft.update({"items": [item for item in draft["items"] if item.get("id") != item_id]}))
            new_domain_event(APP_ITEM_REMOVED, item_id, {"itemId": item_id})
            return self.send_payload(200, {"items": store["items"]})

        if path == "/api/stocks" and self.command == "GET":
            symbols = []
            for symbol in str(first_query(query, "symbols") or "").split(","):
                cleaned = symbol.strip()
                if cleaned and cleaned not in symbols:
                    symbols.append(cleaned)
            return self.send_payload(200, {
                "stocks": [stock_snapshot(symbol) for symbol in symbols[:12]],
                "source": "Quotes: Stooq/Naver Finance, News: multi-channel RSS/GDELT",
                "fetchedAt": now(),
            })

        self.send_payload(404, {"error": "API를 찾지 못했습니다."})

    def serve_static(self, path: str):
        target = "/index.html" if path == "/" else path
        file_path = (PUBLIC_DIR / target.lstrip("/")).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            return self.send_payload(403, "Forbidden", "text/plain; charset=utf-8")
        if file_path.exists() and file_path.is_dir():
            if not path.endswith("/"):
                return self.send_redirect(path + "/")
            file_path = file_path / "index.html"
        if not file_path.exists() or file_path.is_dir():
            return self.send_payload(404, "Not found", "text/plain; charset=utf-8")
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
        mutable_app_assets = {
            "index.html",
            "service-worker.js",
            "manifest.webmanifest",
            "app.js",
            "app-default-settings.js",
            "web-runtime.js",
            "styles.css",
            "live-target.json",
        }
        cache_control = "no-cache" if file_path.name in mutable_app_assets else "public, max-age=31536000, immutable"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return
        compressed = False
        if len(data) >= 1024 and self.accepts_gzip() and content_type.startswith(("text/", "application/javascript", "application/json")):
            data = gzip.compress(data, compresslevel=6)
            compressed = True
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript", "application/json")) else ""))
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


MAX_PORT_FALLBACK_ATTEMPTS = 20


def port_fallback_enabled(value: str = None) -> bool:
    configured = value if value is not None else os.environ.get("ALLOW_PORT_FALLBACK")
    return str(configured or "").strip().lower() not in {"0", "false", "no", "off"}


def bind_web_server(host: str, port: int, allow_port_fallback: bool = True, server_factory=None):
    factory = server_factory or ReusableThreadingHTTPServer
    requested_port = int(port)
    attempt_count = MAX_PORT_FALLBACK_ATTEMPTS if allow_port_fallback else 1
    last_error = None

    for offset in range(attempt_count):
        candidate_port = requested_port + offset
        if candidate_port > 65535:
            break
        try:
            return factory((host, candidate_port), DigitalTwinHandler), candidate_port
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            last_error = error

    final_port = min(65535, requested_port + attempt_count - 1)
    message = "Address already in use for ports " + str(requested_port) + "-" + str(final_port)
    if last_error is not None:
        raise OSError(errno.EADDRINUSE, message) from last_error
    raise OSError(errno.EADDRINUSE, message)


def serve(host: str = "", port: int = 3000):
    selected_host = host or os.environ.get("HOST") or "127.0.0.1"
    selected_port = int(port or os.environ.get("PORT") or 3000)
    requested_port = selected_port
    server, selected_port = bind_web_server(
        selected_host,
        selected_port,
        allow_port_fallback=port_fallback_enabled(),
    )
    display_host = "127.0.0.1" if selected_host in {"", "0.0.0.0"} else selected_host
    if selected_port != requested_port:
        print(
            "Orbit Alpha requested port " + str(requested_port) + " is occupied; using port " + str(selected_port),
            flush=True,
        )
    print("Orbit Alpha Python server running at http://" + display_host + ":" + str(selected_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
