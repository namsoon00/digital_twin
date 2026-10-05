"""Bounded retry delays for evidence capture, before any model call."""


def retry_delays(capability, error_kind, failures):
    if error_kind == "LocalAICapacityUnavailable":
        return min(300, 30 * max(1, failures)), 300
    capture_failure = capability == "observe" and (
        error_kind.startswith("evidence-read:")
        or error_kind in {"evidence-contract:graph-changed-before-capture",
                          "evidence-contract:graph-changed-during-capture"}
    )
    # Three attempts still terminate the task. A durable successor prevents a
    # temporary graph outage from pausing a subject for the general six hours.
    # Model/quality/ownership failures retain the slower policy.
    if capture_failure:
        return (30 if failures <= 1 else 120), 1800
    return 1800 * min(max(1, failures), 3), 21600
