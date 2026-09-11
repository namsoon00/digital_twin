"""Ordered HTTP dispatch without a service locator or module namespace injection."""

from dataclasses import dataclass
from typing import Callable, Dict, List, Protocol, Tuple


Query = Dict[str, List[str]]
NOT_HANDLED = object()


class Request(Protocol):
    command: str

    def read_json_body(self) -> Dict[str, object]: ...

    def send_payload(self, status: int, payload, **kwargs): ...

    def share_access(self): ...

    def ensure_writable(self, message: str) -> bool: ...


Route = Callable[[Request, str, Query], object]


@dataclass(frozen=True)
class ApiRouter:
    routes: Tuple[Route, ...]

    def dispatch(self, request: Request, path: str, query: Query):
        for route in self.routes:
            result = route(request, path, query)
            # A sent response (including None) must stop dispatch. Only an
            # explicit miss may continue to a less-specific route.
            if result is not NOT_HANDLED:
                return result
        return NOT_HANDLED
