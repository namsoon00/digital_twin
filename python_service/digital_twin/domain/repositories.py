import inspect
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

from .accounts import AccountConfig
from .events import DomainEvent
from .investment_research import NewsCollectionTarget, ResearchEvidence
from .investment_brain import DecisionEpisode, LearningProposal, NovelHypothesisProposal, ObservedOutcome
from .investment_evidence_governance import ResearchRun
from .investment_calendar import InvestmentCalendarEvent
from .ontology_contracts import PortfolioOntology
from .portfolio import AccountSnapshot, AlertEvent, Position
from .symbol_universe import ListedSymbol


class AccountRepository(Protocol):
    def load(self) -> List[AccountConfig]:
        ...

    def load_all(self) -> List[AccountConfig]:
        ...

    def load_saved(self) -> List[AccountConfig]:
        ...

    def upsert(self, account: AccountConfig) -> None:
        ...

    def remove(self, account_id: str) -> bool:
        ...


class SnapshotProvider(Protocol):
    def build_snapshot(self, account: AccountConfig) -> AccountSnapshot:
        ...


class MonitorStateRepository(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...

    @property
    def sent(self) -> Dict[str, object]:
        ...

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        ...

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        ...

    def write(self) -> None:
        ...


class MonitorSnapshotReader(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...


class SnapshotMonitor(Protocol):
    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        ...

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        ...


class NotificationGateway(Protocol):
    def send_events(self, events: List[AlertEvent], dry_run: bool = False, accounts=None):
        ...


@dataclass
class MonitoringCycleRecordResult:
    delivered: bool
    queued: int = 0
    reason: str = ""


class MonitoringCycleRecorder(Protocol):
    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
    ) -> MonitoringCycleRecordResult:
        ...


@dataclass
class MonitorAccountJob:
    account_id: str
    status: str = "pending"
    priority: int = 100
    next_run_at: str = ""
    locked_by: str = ""
    locked_until: str = ""
    attempts: int = 0
    last_started_at: str = ""
    last_finished_at: str = ""
    last_error: str = ""
    updated_at: str = ""


class MonitorAccountJobRepository(Protocol):
    def sync_accounts(self, accounts: Iterable[AccountConfig], default_interval_seconds: int) -> None:
        ...

    def claim_due(
        self,
        limit: int,
        worker_id: str,
        lock_seconds: int,
        default_interval_seconds: int,
    ) -> List[MonitorAccountJob]:
        ...

    def mark_done(self, account_id: str, next_run_at: str) -> None:
        ...

    def mark_failed(self, account_id: str, error: str, next_run_at: str) -> None:
        ...

    def summary(self) -> Dict[str, object]:
        ...


ONTOLOGY_GRAPH_REPOSITORY_CONTRACT: Dict[str, Tuple[str, ...]] = {
    "active_tbox_metadata": (),
    "save_graph": ("graph",),
    "seed_ontology": ("payload",),
    "rulebox_snapshot": (),
    "save_rulebox": ("payload",),
    "run_rulebox": ("payload",),
    "inferencebox_snapshot": ("symbols", "limit"),
    "save_rule_change_candidates": ("candidates", "context"),
}


@runtime_checkable
class OntologyGraphRepository(Protocol):
    def active_tbox_metadata(self) -> Dict[str, object]:
        ...

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        ...

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def rulebox_snapshot(self) -> Dict[str, object]:
        ...

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        ...

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        ...


def ontology_graph_repository_contract_errors(repository: object) -> List[str]:
    errors: List[str] = []
    for method_name, expected_parameters in ONTOLOGY_GRAPH_REPOSITORY_CONTRACT.items():
        method = getattr(repository, method_name, None)
        if not callable(method):
            errors.append(method_name + " is missing or not callable")
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            continue
        actual_parameters = [
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.name != "self"
            and parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ]
        if tuple(actual_parameters[:len(expected_parameters)]) != expected_parameters:
            errors.append(
                method_name
                + " signature mismatch: expected "
                + ", ".join(expected_parameters)
                + " got "
                + ", ".join(actual_parameters)
            )
    return errors


def ensure_ontology_graph_repository_contract(repository: object, label: str = "ontology graph repository"):
    errors = ontology_graph_repository_contract_errors(repository)
    if errors:
        raise TypeError(label + " does not satisfy OntologyGraphRepository contract: " + "; ".join(errors))
    return repository


class OntologyProjectionRecorder(Protocol):
    def record_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        ...


class DecisionEpisodeRepository(Protocol):
    def save(self, episode: DecisionEpisode) -> DecisionEpisode:
        ...

    def get(self, episode_id: str) -> Optional[DecisionEpisode]:
        ...

    def list(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[DecisionEpisode]:
        ...

    def record_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str = "",
    ) -> List[ObservedOutcome]:
        ...

    def save_learning_proposal(self, proposal: LearningProposal) -> LearningProposal:
        ...

    def list_learning_proposals(self, status: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def review_learning_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        ...


class InvestmentResearchRepository(Protocol):
    def save_run(self, run: ResearchRun) -> ResearchRun:
        ...

    def list_runs(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def save_hypothesis_proposal(self, proposal: NovelHypothesisProposal) -> NovelHypothesisProposal:
        ...

    def list_hypothesis_proposals(self, status: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def review_hypothesis_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        ...


class SymbolUniverseRepository(Protocol):
    def upsert_many(self, symbols: Iterable[ListedSymbol]) -> int:
        ...

    def counts_by_market(self) -> Dict[str, int]:
        ...

    def latest_seen_by_market(self) -> Dict[str, str]:
        ...

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> List[ListedSymbol]:
        ...

    def search_count(self, query: str = "", market: str = "") -> int:
        ...

    def get(self, symbol: str, market: str = "") -> Optional[ListedSymbol]:
        ...

    def mark_source(self, market: str, source: str, source_url: str, status: str, count: int = 0, error: str = "") -> None:
        ...

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        ...

    def source_states(self) -> List[Dict[str, object]]:
        ...


class InvestmentCalendarRepository(Protocol):
    def upsert(self, event: InvestmentCalendarEvent) -> InvestmentCalendarEvent:
        ...

    def get(self, event_id: str):
        ...

    def delete(self, event_id: str) -> bool:
        ...

    def list(
        self,
        from_at: str = "",
        to_at: str = "",
        status: str = "",
        symbol: str = "",
        event_type: str = "",
        limit: int = 200,
    ) -> List[InvestmentCalendarEvent]:
        ...

    def reminder_candidates(self, now_at: str = "", lookback_minutes: int = 180) -> List[InvestmentCalendarEvent]:
        ...

    def summary(self) -> Dict[str, object]:
        ...


class SymbolSourceGateway(Protocol):
    def fetch_market_symbols(self, market: str) -> List[ListedSymbol]:
        ...

    def source_descriptor(self, market: str) -> Dict[str, str]:
        ...


class MarketQuoteRepository(Protocol):
    def load(self, provider: str, account_id: str, symbol: str) -> Dict[str, object]:
        ...

    def load_many(self, provider: str, account_id: str, symbols: Iterable[str]) -> Dict[str, Dict[str, object]]:
        ...

    def save(self, provider: str, account_id: str, symbol: str, payload: Dict[str, object]) -> None:
        ...

    def summary(self, provider: str, account_id: str) -> Dict[str, object]:
        ...

    def stale_universe_symbols(
        self,
        provider: str,
        account_id: str,
        markets: Iterable[str],
        limit: int = 200,
        max_age_minutes: int = 240,
    ) -> List[Dict[str, object]]:
        ...


class ResearchEvidenceRepository(Protocol):
    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        ...

    def upsert_many_with_events(
        self,
        items: Iterable[ResearchEvidence],
        event_builder: Callable[[int, List[str], List[ResearchEvidence]], Iterable[DomainEvent]],
    ) -> Tuple[int, List[DomainEvent]]:
        ...

    def latest(self, symbol: str = "", kind: str = "", limit: int = 50) -> List[ResearchEvidence]:
        ...

    def delete(self, evidence_id: str) -> bool:
        ...

    def delete_stale_news(self, cutoff_iso: str, limit: int = 500) -> int:
        ...

    def summary(self) -> Dict[str, object]:
        ...


class ResearchEvidenceGateway(Protocol):
    def collect_for_target(
        self,
        target: NewsCollectionTarget,
        source_types: Iterable[str] = None,
    ) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        ...

    def providers(self) -> List[str]:
        ...


class MarketDataProvider(Protocol):
    def fetch_access_token(self) -> str:
        ...

    def fetch_positions(self) -> Tuple[str, str, List[Position], float, str, List[Position]]:
        ...

    def fetch_focus_targets(self) -> Tuple[str, str, str, List[Position], List[Position]]:
        ...

    def fetch_prices(self, token: str, symbols: Iterable[str]) -> Tuple[Dict[str, Dict[str, object]], str]:
        ...

    def fetch_daily_candles(self, token: str, symbol: str) -> Tuple[List[Dict[str, object]], str]:
        ...

    def merge_market_data(
        self,
        position: Position,
        quote: Dict[str, object],
        indicators: Dict[str, object],
        cached: Dict[str, object],
        quote_live: bool = False,
        indicators_live: bool = False,
    ) -> Position:
        ...


MarketDataProviderFactory = Callable[[AccountConfig, MarketQuoteRepository], MarketDataProvider]
