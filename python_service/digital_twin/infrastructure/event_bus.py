import json
import os
from collections import defaultdict
from typing import Callable, DefaultDict, Iterable, List

from digital_twin.shared_kernel.events import DomainEvent
from .settings import data_dir


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self, raise_handler_errors: bool = False, recorder: EventHandler = None,
                 history_limit: int = 100):
        self.handlers: DefaultDict[str, List[EventHandler]] = defaultdict(list)
        self.published: List[DomainEvent] = []
        self.handler_errors: List[Exception] = []
        self.raise_handler_errors = raise_handler_errors
        self.recorder = recorder
        self.history_limit = max(0, int(history_limit))

    def remember(self, event: DomainEvent) -> None:
        if self.history_limit:
            self.published.append(event)
            del self.published[:-self.history_limit]

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self.handlers[event_name].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        self.subscribe("*", handler)

    def dispatch(self, event: DomainEvent) -> None:
        for handler in self.handlers.get(event.name, []) + self.handlers.get("*", []):
            try:
                handler(event)
            except Exception as error:  # noqa: BLE001 - event handlers must not break the publisher by default.
                # Exception tracebacks retain handler frames, including large
                # graph inputs. Keep a bounded description, never that frame.
                self.handler_errors.append(RuntimeError(type(error).__name__ + ": " + str(error)[:600]))
                del self.handler_errors[:-100]
                if self.raise_handler_errors:
                    raise

    def publish(self, event: DomainEvent) -> None:
        if self.recorder:
            self.recorder(event)
        self.remember(event)
        self.dispatch(event)

    def dispatch_recorded(self, event: DomainEvent) -> None:
        self.remember(event)
        self.dispatch(event)

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
    from .operational_store import event_log

    # The durable event log owns history. Runtime dispatch must not keep a
    # second lifetime-long copy of every event payload in the worker heap.
    return EventBus(recorder=event_log().handle, history_limit=0)
