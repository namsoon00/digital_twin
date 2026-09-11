"""Web telemetry boundary."""

from datetime import datetime
from datetime import timezone
from digital_twin.infrastructure.api_performance import ApiPerformanceRegistry


WEB_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


API_PERFORMANCE = ApiPerformanceRegistry()
