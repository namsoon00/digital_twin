"""Connection lifetime is explicitly supplied by the coordinator."""

from typing import Callable, ContextManager
from digital_twin.infrastructure.transaction_port import BoundWriteConnection

ConnectionFactory = Callable[[], ContextManager[BoundWriteConnection]]
