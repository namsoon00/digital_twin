"""Runtime composition for the versioned reasoning engine control plane."""

from digital_twin.modules.reasoning.public import ReasoningEnginePlatformService
from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineRegistryStore
from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineComparisonStore, MySQLReasoningEngineJobStore, MySQLReasoningShadowJobStore
from .runtime_identity import runtime_identity


def build_reasoning_engine_platform(settings=None):
    configured = dict(settings or {})
    configured["_runtimeIdentity"] = runtime_identity()
    return ReasoningEnginePlatformService(
        MySQLReasoningEngineRegistryStore(configured),
        configured,
        comparison_store=MySQLReasoningEngineComparisonStore(configured),
        shadow_queue=MySQLReasoningShadowJobStore(configured),
        independent_job_store=MySQLReasoningEngineJobStore(configured),
    )
