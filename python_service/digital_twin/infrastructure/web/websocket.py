"""Web websocket boundary."""

from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.events import REALTIME_HUB
from digital_twin.infrastructure.web.events import read_websocket_frame
from digital_twin.infrastructure.web.events import realtime_status_payload
from digital_twin.infrastructure.web.events import websocket_accept_key
import select
import socket


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
