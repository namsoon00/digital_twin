import json
import os
from collections import defaultdict
from typing import Callable, DefaultDict, Iterable, List

from ..domain.events import DomainEvent
from .settings import data_dir


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self, raise_handler_errors: bool = False):
        self.handlers: DefaultDict[str, List[EventHandler]] = defaultdict(list)
        self.published: List[DomainEvent] = []
        self.handler_errors: List[Exception] = []
        self.raise_handler_errors = raise_handler_errors

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self.handlers[event_name].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        self.subscribe("*", handler)

    def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        for handler in self.handlers.get(event.name, []) + self.handlers.get("*", []):
            try:
                handler(event)
            except Exception as error:  # noqa: BLE001 - event handlers must not break the publisher by default.
                self.handler_errors.append(error)
                if self.raise_handler_errors:
                    raise

    def publish_all(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            self.publish(event)


class JsonEventLog:
    def __init__(self, path=None):
        self.path = path or data_dir() / "domain-events.jsonl"

    def handle(self, event: DomainEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def default_event_bus() -> EventBus:
    from .sqlite_operational import SQLiteEventLog

    bus = EventBus()
    bus.subscribe_all(SQLiteEventLog().handle)
    return bus
