"""Translate input-read failures without leaking private query/provider text."""
from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, EvidenceReadError


def read_evidence_stage(stage, operation):
    try:
        return operation()
    except (EvidenceContractError, EvidenceReadError):
        raise
    except Exception as error:
        raise EvidenceReadError(stage, error) from error
