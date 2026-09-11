"""Web capital flow boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.accounts.domain.accounts import split_symbols
from digital_twin.modules.market_data.public import CapitalFlowService
from typing import Dict
from typing import List


def capital_flow_api_payload(
    query: Dict[str, List[str]],
    *,
    snapshot: Dict[str, object] = None,
    subject_id: str = "",
    quality_only: bool = False,
) -> Dict[str, object]:
    """Read capital movement separately from the decision-lineage flow API."""

    settings = operational_read_settings()
    store = stores.market_time_series_store(settings)
    if quality_only:
        return {
            "contract": "capital-flow-quality-v1",
            **store.capital_flow_quality(safe_int(first_query(query, "days"), 30, 1, 3650)),
        }
    requested_symbols = split_symbols(first_query(query, "symbols") or first_query(query, "symbol") or "")
    if subject_id:
        requested_symbols = [str(subject_id or "").upper().strip()]
    source_snapshot = dict(snapshot or {})
    toss = source_snapshot.get("toss") if isinstance(source_snapshot.get("toss"), dict) else {}
    positions_available = isinstance(toss.get("positions"), list)
    positions = list(toss.get("positions") or []) if positions_available else []
    service = CapitalFlowService(store)
    payload = service.summary(
        symbols=requested_symbols,
        market=str(first_query(query, "market") or ""),
        window_days=safe_int(first_query(query, "windowDays") or first_query(query, "window"), 5, 1, 20),
        observed_after=str(first_query(query, "observedAfter") or ""),
        as_of=str(first_query(query, "asOf") or ""),
        limit=safe_int(first_query(query, "limit"), 10000, 1, 50000),
        positions=positions,
        positions_available=positions_available,
        position_snapshot_as_of=str(source_snapshot.get("generatedAt") or ""),
    )
    payload["readOnly"] = True
    payload["source"] = "capital-flow-observations"
    return payload
