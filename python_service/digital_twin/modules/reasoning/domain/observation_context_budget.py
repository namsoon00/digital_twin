"""Separate bounded historical context from the unchanged source-fact budget."""
import json

VERSION = "scoped-history-budget-v1"
FIELD = "historicalContextPolicy"
MAX_BYTES = 64 * 1024
FIELDS = frozenset({"lastDeliveredNotification", "deliveredNotificationContext",
                    "reviewedQuestionContext", "analystMemory"})


def historical_bytes(packet):
    return len(json.dumps({key: packet[key] for key in FIELDS if key in packet},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def validate(packet):
    if packet.get(FIELD) is None:
        return
    from .observation_evidence import EvidenceContractError
    if packet.get(FIELD) != VERSION:
        raise EvidenceContractError("unsupported historical context budget")
    if historical_bytes(packet) > MAX_BYTES:
        raise EvidenceContractError("historical context exceeds context budget")


def source_fields(packet):
    # No current fact, coverage, source clock or read receipt is excluded.
    excluded = FIELDS if packet.get(FIELD) == VERSION else frozenset()
    return {key: value for key, value in packet.items() if key not in excluded | {"researchKnowledge"}}
