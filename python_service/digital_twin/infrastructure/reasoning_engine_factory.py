"""Runtime composition for the versioned reasoning engine control plane."""

from ..application.reasoning_engine_platform import ReasoningEnginePlatformService
from .mysql_versioned_runtime import MySQLReasoningEngineRegistryStore


def build_reasoning_engine_platform(settings=None):
    configured = dict(settings or {})
    return ReasoningEnginePlatformService(MySQLReasoningEngineRegistryStore(configured), configured)
