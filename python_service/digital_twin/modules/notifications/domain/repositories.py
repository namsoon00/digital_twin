"""Repository capabilities owned by notifications."""

from typing import List, Protocol
from digital_twin.domain.portfolio import AlertEvent


class NotificationGateway(Protocol):
    def send_events(self, events: List[AlertEvent], dry_run: bool = False, accounts=None):
        ...
