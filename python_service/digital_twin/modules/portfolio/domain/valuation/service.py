"""Use-case-neutral entry point for deterministic valuation models."""

from dataclasses import dataclass, field
from typing import Dict, List

from digital_twin.modules.market_data.contracts import number
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.portfolio.domain.valuation.quality import apply_valuation_quality_gate
from digital_twin.modules.portfolio.domain.valuation.registry import registered_valuation_model_rows
from digital_twin.modules.portfolio.domain.valuation.snapshot import bind_valuation_snapshot


VALUATION_MODEL_SERVICE_VERSION = "valuation-model-service-v1"


@dataclass(frozen=True)
class ValuationModelRequest:
    position: Position
    external_signals: Dict[str, object] = field(default_factory=dict)
    settings: Dict[str, object] = field(default_factory=dict)
    valuation_at: str = ""
    knowledge_cutoff_at: str = ""
    source_snapshot_id: str = ""


@dataclass(frozen=True)
class ValuationModelResult:
    symbol: str
    rows: List[Dict[str, object]]
    status: str
    model_service_version: str = VALUATION_MODEL_SERVICE_VERSION

    @property
    def has_fair_value(self) -> bool:
        return any(number(row.get("fairValue")) > 0.0 for row in self.rows)

    @property
    def decision_eligible(self) -> bool:
        return any(bool(row.get("valuationDecisionEligible")) for row in self.rows)


class ValuationModelService:
    """Select and execute the valuation model appropriate to the instrument.

    The returned rows are facts and calculation traces. They never contain a
    buy, sell, hold, or reduce action.
    """

    def evaluate(self, request: ValuationModelRequest) -> ValuationModelResult:
        position = request.position
        rows = []
        for source_row in registered_valuation_model_rows(
            position,
            dict(request.external_signals or {}),
            dict(request.settings or {}),
        ):
            checked = apply_valuation_quality_gate({
                **source_row,
                "valuationModelServiceVersion": VALUATION_MODEL_SERVICE_VERSION,
                "calculationOwner": "valuation-bounded-context",
            })
            rows.append(bind_valuation_snapshot(
                checked,
                symbol=position.symbol,
                security_line=checked.get("securityLine") or position.symbol,
                valuation_currency=checked.get("valuationCurrency") or position.currency,
                quote_value=position.current_price,
                quote_as_of=getattr(position, "updated_at", ""),
                valuation_at=request.valuation_at or getattr(position, "updated_at", ""),
                knowledge_cutoff_at=request.knowledge_cutoff_at or checked.get("valuationAsOf"),
                model_release_id=checked.get("valuationModelId") or checked.get("valuationMethod"),
                source_snapshot_id=request.source_snapshot_id or getattr(position, "valuation_snapshot_id", ""),
            ))
        if not rows:
            status = "unavailable"
        elif any(number(row.get("fairValue")) > 0.0 for row in rows):
            status = "calculated"
        elif any(str(row.get("valuationQualityStatus") or "") == "blocked" for row in rows):
            status = "blocked-invalid-data"
        else:
            status = "blocked-missing-inputs"
        return ValuationModelResult(
            symbol=str(position.symbol or "").upper().strip(),
            rows=[dict(row) for row in rows],
            status=status,
        )


def evaluate_valuation_models(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> List[Dict[str, object]]:
    """Compatibility-friendly functional facade used by graph projection."""

    request = ValuationModelRequest(
        position=position,
        external_signals=dict(external_signals or {}),
        settings=dict(settings or {}),
    )
    return ValuationModelService().evaluate(request).rows
