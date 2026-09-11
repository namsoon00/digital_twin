"""Operations HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.runtime_identity import runtime_identity
from digital_twin.infrastructure.web.adapters.external_data import external_data_status_payload
from digital_twin.infrastructure.web.adapters.operations import console_operations_health_api_payload
from digital_twin.infrastructure.web.adapters.platforms import time_series_platform_status_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.events import realtime_status_payload
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from digital_twin.infrastructure.web.telemetry import API_PERFORMANCE
from digital_twin.infrastructure.web.telemetry import WEB_PROCESS_STARTED_AT
from typing import Callable


@dataclass(frozen=True)
class OperationsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    console_operations_health_api_payload: Callable[..., object] = console_operations_health_api_payload
    external_data_status_payload: Callable[..., object] = external_data_status_payload
    realtime_status_payload: Callable[..., object] = realtime_status_payload
    runtime_identity: Callable[..., object] = runtime_identity
    time_series_platform_status_payload: Callable[..., object] = time_series_platform_status_payload

    def route_version(self, request, path: str, query: Query):
        if path == "/api/version" and request.command == "GET":
            return request.send_payload(200, {
                **self.runtime_identity(),
                "startedAt": WEB_PROCESS_STARTED_AT,
            })

        if path == "/api/operations/performance" and request.command == "GET":
            return request.send_payload(200, API_PERFORMANCE.snapshot(), cache_control="no-store")
        return NOT_HANDLED

    def route_time_series_platform_status(self, request, path: str, query: Query):
        if path == "/api/time-series-platform/status" and request.command == "GET":
            return request.send_payload(200, self.time_series_platform_status_payload())
        return NOT_HANDLED

    def route_operations_health(self, request, path: str, query: Query):
        if path == "/api/operations/health" and request.command == "GET":
            return request.send_payload(200, self.console_operations_health_api_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")
        return NOT_HANDLED

    def route_realtime_status(self, request, path: str, query: Query):
        if path == "/api/realtime/status" and request.command == "GET":
            return request.send_payload(200, self.realtime_status_payload())

        if path == "/api/external-data/status" and request.command == "GET":
            return request.send_payload(200, self.external_data_status_payload(
                force=request_bool(first_query(query, "refresh"), False),
            ), cache_control="no-store")
        return NOT_HANDLED
