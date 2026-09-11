"""Query vocabulary for one instrument's valuation read model."""

from dataclasses import dataclass

from digital_twin.modules.read_models.contracts import normalize_instrument_symbol


@dataclass(frozen=True)
class InstrumentValuationQuery:
    symbol: str
    account_id: str = ""

    def normalized(self) -> "InstrumentValuationQuery":
        return InstrumentValuationQuery(
            symbol=normalize_instrument_symbol(self.symbol),
            account_id=str(self.account_id or "").strip()[:191],
        )
