import json
from pathlib import Path
from typing import Dict, List

from ..domain.investment_strategy_proposals import InvestmentStrategyProposal
from ..domain.ontology_experiments import OntologyExperiment
from .settings import data_dir


class JsonOntologyExperimentStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or (data_dir() / "ontology-lab.json"))

    def list(self) -> List[OntologyExperiment]:
        payload = self.load()
        experiments = [
            OntologyExperiment.from_dict(item)
            for item in payload.get("experiments") or []
            if isinstance(item, dict)
        ]
        return sorted(experiments, key=lambda item: item.updated_at or item.created_at or "", reverse=True)

    def get(self, experiment_id: str) -> OntologyExperiment:
        target = str(experiment_id or "").strip()
        for experiment in self.list():
            if experiment.experiment_id == target:
                return experiment
        return None

    def save(self, experiment: OntologyExperiment) -> None:
        payload = self.load()
        rows = [
            item
            for item in payload.get("experiments") or []
            if isinstance(item, dict) and str(item.get("id") or "") != experiment.experiment_id
        ]
        rows.append(experiment.to_dict())
        payload["experiments"] = rows
        self.write(payload)

    def load(self) -> Dict[str, object]:
        if not self.path.exists():
            return {"experiments": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"experiments": []}
        if not isinstance(payload, dict):
            return {"experiments": []}
        payload.setdefault("experiments", [])
        return payload

    def write(self, payload: Dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload if isinstance(payload, dict) else {"experiments": []}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class JsonInvestmentStrategyProposalStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or (data_dir() / "investment-strategy-proposals.json"))

    def list(self) -> List[InvestmentStrategyProposal]:
        payload = self.load()
        proposals = [
            InvestmentStrategyProposal.from_dict(item)
            for item in payload.get("proposals") or []
            if isinstance(item, dict)
        ]
        return sorted(proposals, key=lambda item: item.updated_at or item.created_at or "", reverse=True)

    def get(self, proposal_id: str) -> InvestmentStrategyProposal:
        target = str(proposal_id or "").strip()
        for proposal in self.list():
            if proposal.proposal_id == target:
                return proposal
        return None

    def save(self, proposal: InvestmentStrategyProposal) -> None:
        payload = self.load()
        rows = [
            item
            for item in payload.get("proposals") or []
            if isinstance(item, dict) and str(item.get("id") or "") != proposal.proposal_id
        ]
        rows.append(proposal.to_dict())
        payload["proposals"] = rows
        self.write(payload)

    def load(self) -> Dict[str, object]:
        if not self.path.exists():
            return {"proposals": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"proposals": []}
        if not isinstance(payload, dict):
            return {"proposals": []}
        payload.setdefault("proposals", [])
        return payload

    def write(self, payload: Dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload if isinstance(payload, dict) else {"proposals": []}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
