from typing import Callable, Dict

from ..domain.investment_analysis import build_investment_analysis


class InvestmentAnalysisService:
    def __init__(self, snapshot_provider: Callable[..., Dict[str, object]]):
        self.snapshot_provider = snapshot_provider

    def snapshot(self, mock: bool = False, watchlist_symbols: str = "") -> Dict[str, object]:
        flow_snapshot = self.snapshot_provider(mock=mock, watchlist_symbols=watchlist_symbols)
        return {
            "investmentAnalysis": build_investment_analysis(flow_snapshot),
            "sourceSnapshot": {
                "generatedAt": flow_snapshot.get("generatedAt"),
                "dataMode": flow_snapshot.get("dataMode"),
                "mock": bool(flow_snapshot.get("mock")),
            },
        }
