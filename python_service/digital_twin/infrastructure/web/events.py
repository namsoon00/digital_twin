"""Web events boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.infrastructure.event_bus import JsonEventLog
from digital_twin.infrastructure.event_bus import default_event_bus
from digital_twin.infrastructure.web.adapters.notification_storage import notification_queue_store
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.modules.market_data.domain.event_types import MONITORING_ALERTS_DETECTED
from digital_twin.modules.market_data.domain.event_types import MONITORING_CYCLE_COMPLETED
from digital_twin.modules.market_data.domain.event_types import MONITORING_SNAPSHOT_COLLECTED
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict
from typing import List
import base64
import hashlib
import json
import threading


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
